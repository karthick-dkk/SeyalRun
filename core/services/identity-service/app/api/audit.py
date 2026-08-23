from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.audithash import verify_chain

from ..audit import audit_payload
from ..database import get_session
from ..deps import require_admin, require_service_token
from ..models import ZAAuditLog
from ..schemas import AuditLogOut

router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_service_token), Depends(require_admin)])


@router.get("/logs", response_model=list[AuditLogOut])
async def list_audit_logs(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=100, le=1000),
    offset: int = Query(default=0, ge=0),
    action: str | None = Query(default=None, description="Action prefix, e.g. 'sftp.' for all file operations"),
    result_filter: str | None = Query(default=None, alias="result", description="success | failure"),
):
    """Filtering exists so a specific class of event can be found at all.

    Without it, "show me the file transfers" meant paging through every row in
    the log — and an assessor asking "what left this host, and did anything get
    refused?" is exactly the question the chain is kept for. `action` is a prefix
    match so 'sftp.' returns the whole family (list, download, upload, mkdir,
    rename, delete) rather than requiring six separate queries.
    """
    stmt = select(ZAAuditLog)
    if action:
        stmt = stmt.where(ZAAuditLog.action.startswith(action))
    if result_filter:
        stmt = stmt.where(ZAAuditLog.result == result_filter)
    stmt = stmt.order_by(ZAAuditLog.created_at.desc()).limit(limit).offset(offset)
    return (await session.execute(stmt)).scalars().all()


@router.get("/verify")
async def verify_audit_chain(session: AsyncSession = Depends(get_session)):
    """Recompute the tamper-evident hash chain over all chained rows and report
    the first break (if any). A compromised admin editing or deleting a historical
    row will surface here as ok=false with the broken seq."""
    rows = (
        await session.execute(
            select(ZAAuditLog).where(ZAAuditLog.seq.isnot(None)).order_by(ZAAuditLog.seq.asc())
        )
    ).scalars().all()
    chain = [
        {
            "seq": r.seq,
            "prev_hash": r.prev_hash,
            "entry_hash": r.entry_hash,
            "payload": audit_payload(
                r.user_id, r.username, r.action, r.resource_type,
                r.resource_id, r.details, r.ip_address, r.created_at,
                session_id=r.session_id, result=r.result,
            ),
        }
        for r in rows
    ]
    return verify_chain(chain)
