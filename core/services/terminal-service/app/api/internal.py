"""Internal endpoints — consumed by recording-service."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import require_service_token
from ..models import ZASSHSession

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(require_service_token)])


@router.get("/sessions/{session_id}")
async def get_session_internal(session_id: str, db: AsyncSession = Depends(get_session)):
    """Return session metadata — called by recording-service to avoid cross-DB queries."""
    result = await db.execute(select(ZASSHSession).where(ZASSHSession.id == session_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    return {
        "id": row.id,
        "user_id": row.user_id,
        "username": row.username,
        "host_id": row.host_id,
        "host_name": row.host_name,
        "status": row.status,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
    }
