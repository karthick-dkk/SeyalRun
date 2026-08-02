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


async def clear(session: AsyncSession, key: str) -> None:
    """Clear lockout state after a successful login."""
    from .models import ZALoginAttempt

    row = await session.get(ZALoginAttempt, key)
    if row is not None:
        await session.delete(row)
        await session.commit()
