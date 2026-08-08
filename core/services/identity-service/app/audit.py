"""Audit log writer with a tamper-evident hash chain (T9).

Every audit entry in the whole platform funnels here (inventory and other
services forward to identity's /internal/audit, which calls log_action), so this
is the single chain writer. Each row is linked to its predecessor by
entry_hash = H(seq, prev_hash, payload); editing or deleting any historical row
breaks every later hash. See libs/audithash and the /audit/verify endpoint.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from libs.audithash import GENESIS, compute_entry_hash

from .config import get_settings

# Logger name IS the routing category the shipper classifies on.
_audit_log = logging.getLogger("seyalrun.audit")
from .models import ZAAuditLog

# Fixed advisory-lock key (Postgres) / name (MySQL) that serializes chain writes.
_PG_LOCK_KEY = 0x5359414C  # "SYAL"
_MYSQL_LOCK_NAME = "za_audit_chain"


@asynccontextmanager
async def _chain_lock(session: AsyncSession, db_engine: str):
    """Serialize chain appends so two concurrent writers can't fork the chain."""
    if db_engine == "postgres":
        # Transaction-scoped: auto-released on commit/rollback.
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _PG_LOCK_KEY})
        yield
    elif db_engine == "mysql":
        # Connection-scoped: must release explicitly (survives commit).
        await session.execute(text("SELECT GET_LOCK(:n, 10)"), {"n": _MYSQL_LOCK_NAME})
        try:
            yield
        finally:
            await session.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": _MYSQL_LOCK_NAME})
    else:
        yield


def audit_payload(user_id, username, action, resource_type, resource_id, details, ip_address, created_at,
                   session_id=None, result=None) -> dict:
    """The exact field set bound into the hash (must match /audit/verify). session_id/
    result are newer fields (PCI DSS Phase A) — omitted keys never existed on rows
    written before this change, so .get()-style defaults elsewhere must not assume
    presence; the hash itself is unaffected for those older rows since it was
    computed once, at write time, over whatever fields existed then."""
    payload = {
        "user_id": user_id or "",
        "username": username or "",
        "action": action,
        "resource_type": resource_type or "",
        "resource_id": resource_id or "",
        "details": details or {},
        "ip_address": ip_address or "",
        # Second precision so the hash is identical whether stored in Postgres
        # (microsecond timestamptz) or MySQL (second-precision DATETIME) — otherwise
        # verify would falsely fail on MySQL after the stored value is truncated.
        "created_at": created_at.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
    }
    if session_id is not None:
        payload["session_id"] = session_id
    if result is not None:
        payload["result"] = result
    return payload


async def log_action(
    session: AsyncSession,
    *,
    user_id: str | None,
    username: str,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    details: dict | None = None,
    ip_address: str = "",
    session_id: str | None = None,
    result: str | None = None,
) -> None:
    db_engine = get_settings().db_engine
    created = datetime.now(timezone.utc)
    payload = audit_payload(user_id, username, action, resource_type, resource_id, details, ip_address, created,
                            session_id=session_id, result=result)

    async with _chain_lock(session, db_engine):
        last = (
            await session.execute(
                select(ZAAuditLog.seq, ZAAuditLog.entry_hash)
                .where(ZAAuditLog.seq.isnot(None))
                .order_by(ZAAuditLog.seq.desc())
                .limit(1)
            )
        ).first()
        prev_seq = last[0] if last and last[0] is not None else 0
        prev_hash = last[1] if last and last[1] else GENESIS
        seq = prev_seq + 1
        entry_hash = compute_entry_hash(seq, prev_hash, payload)

        session.add(
            ZAAuditLog(
                user_id=user_id,
                username=username,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details or {},
                ip_address=ip_address,
                created_at=created,
                seq=seq,
                prev_hash=prev_hash,
                entry_hash=entry_hash,
            )
        )
        await session.commit()

    # Emit a categorized log line (logger name = the routing category) so the log
    # shipper can route audit events to the admin-chosen backend (e.g. Elasticsearch).
    # This is a shippable COPY; the authoritative hash-chained record is the DB row above.
    _audit_log.info(
        "%s %s", action, resource_type or "",
        extra={"category": "audit", "action": action, "username": username,
               "resource_type": resource_type, "resource_id": resource_id,
               "ip_address": ip_address, "seq": seq},
    )
