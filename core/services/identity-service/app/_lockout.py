"""DB-backed login brute-force lockout (T15).

Keyed by "username|ip" so a single attacker IP can't lock out a victim across
the whole system, and a distributed guess against one account is still bounded.
State lives in za_login_attempts (survives restarts; no redis needed here).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # runtime imports are deferred so the pure helpers below load standalone
    from sqlalchemy.ext.asyncio import AsyncSession


def attempt_key(username: str, ip: str) -> str:
    return f"{(username or '').lower()}|{ip or ''}"[:320]


def should_reset(locked_until, updated_at, now, window_seconds: int) -> bool:
    """Whether to start a fresh counting window before recording a failure.

    True if a prior lockout has already expired (the user served the lockout, so they get
    a clean set of attempts) OR there was no failed attempt within the sliding window.
    The lock-expiry check is what stops a served lockout from re-locking on the very next
    failure (fail_count would otherwise still be >= max). Pure — unit-testable.
    """
    lock_expired = locked_until is not None and locked_until <= now
    window_stale = updated_at is not None and (now - updated_at) > timedelta(seconds=window_seconds)
    return lock_expired or window_stale


async def check_locked(session: AsyncSession, key: str) -> int | None:
    """Return remaining lockout seconds if currently locked, else None."""
    from .models import ZALoginAttempt

    row = await session.get(ZALoginAttempt, key)
    if row and row.locked_until:
        now = datetime.now(timezone.utc)
        if row.locked_until > now:
            return int((row.locked_until - now).total_seconds())
    return None


async def record_failure(session: AsyncSession, key: str, *, max_failures: int,
                         lockout_seconds: int, window_seconds: int) -> None:
    """Increment the failure counter and lock the key once the threshold is hit."""
    from .models import ZALoginAttempt

    now = datetime.now(timezone.utc)
    row = await session.get(ZALoginAttempt, key)
    if row is None:
        row = ZALoginAttempt(key=key, fail_count=0)
        session.add(row)
    if should_reset(row.locked_until, row.updated_at, now, window_seconds):
        row.fail_count = 0
        row.locked_until = None
    row.fail_count += 1
    row.updated_at = now
    if row.fail_count >= max_failures:
        row.locked_until = now + timedelta(seconds=lockout_seconds)
    await session.commit()


async def distinct_username_failures(session: AsyncSession, ip: str, window_seconds: int) -> int:
    """Count of distinct username|ip rows with a recent failure from this IP — the
    signature of credential-stuffing/password-spraying (many usernames, one
    source), distinct from ordinary lockout (one username, repeated failures).
    PCI DSS Phase B — used to fire a login-spike security alert."""
    from sqlalchemy import func, select

    from .models import ZALoginAttempt

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    result = await session.execute(
        select(func.count()).select_from(ZALoginAttempt).where(
            ZALoginAttempt.key.like(f"%|{ip}"), ZALoginAttempt.updated_at >= cutoff,
        )
    )
    return result.scalar_one()


def aggregate_lockouts(rows, now) -> list[dict]:
    """Collapse per-"username|ip" rows into one entry per username that is CURRENTLY locked.

    ``rows`` is an iterable of (key, fail_count, locked_until). Rows whose lock has
    expired (or never engaged) are ignored. The remaining time reported is the longest
    across that username's IP rows. Pure — unit-testable.
    """
    out: dict[str, dict] = {}
    for key, fail_count, locked_until in rows:
        if locked_until is None or locked_until <= now:
            continue
        username, _, ip = (key or "").rpartition("|")
        entry = out.setdefault(username, {"username": username, "locked_until": locked_until,
                                          "remaining_seconds": 0, "ips": [], "fail_count": 0})
        entry["remaining_seconds"] = max(entry["remaining_seconds"], int((locked_until - now).total_seconds()))
        entry["locked_until"] = max(entry["locked_until"], locked_until)
        entry["fail_count"] = max(entry["fail_count"], fail_count or 0)
        if ip and ip not in entry["ips"]:
            entry["ips"].append(ip)
    return sorted(out.values(), key=lambda e: e["username"])


def username_prefix(username: str) -> str:
    """Key prefix shared by every IP row of one username (see attempt_key)."""
    return f"{(username or '').lower()}|"


async def list_locked(session: AsyncSession) -> list[dict]:
    """Usernames with at least one active lockout row."""
    from sqlalchemy import select

    from .models import ZALoginAttempt

    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(ZALoginAttempt.key, ZALoginAttempt.fail_count, ZALoginAttempt.locked_until)
        .where(ZALoginAttempt.locked_until.isnot(None), ZALoginAttempt.locked_until > now)
    )
    return aggregate_lockouts(result.all(), now)


async def clear_username(session: AsyncSession, username: str) -> int:
    """Delete every IP row for ``username`` (lockout + failure counters). Returns rows removed."""
    from sqlalchemy import delete

    from .models import ZALoginAttempt

    res = await session.execute(
        delete(ZALoginAttempt).where(ZALoginAttempt.key.startswith(username_prefix(username), autoescape=True))
    )
    await session.commit()
    return res.rowcount or 0


async def clear(session: AsyncSession, key: str) -> None:
    """Clear lockout state after a successful login."""
    from .models import ZALoginAttempt

    row = await session.get(ZALoginAttempt, key)
    if row is not None:
        await session.delete(row)
        await session.commit()
