"""SSH Sessions API — create, list, get, terminate, replay."""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_async_session
from ..dependencies import get_current_user
from ..models.domain import SSHSession

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/ssh", tags=["ssh"])


def _to_resp(s: SSHSession) -> dict:
    return {
        "id": str(s.id),
        "user": s.user,
        "asset_id": s.asset_id,
        "asset_name": s.asset_name,
        "asset_address": s.asset_address,
        "ssh_username": s.ssh_username,
        "status": s.status,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "ended_at": s.ended_at.isoformat() if s.ended_at else None,
        "duration_seconds": s.duration_seconds,
        "command_count": s.command_count,
        "exit_reason": s.exit_reason,
    }


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    body: dict,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> dict:
    """
    Create a pending SSH session and return the WebSocket URL.
    Body: {asset_id, asset_name, asset_address, ssh_username, ssh_password, gateway?}
    """
    asset_address = body.get("asset_address", "")
    ssh_username = body.get("ssh_username", "")
    if not asset_address or not ssh_username:
        raise HTTPException(status_code=400, detail="asset_address and ssh_username are required")

    # Store credentials temporarily in the recording JSONB field
    # They are consumed and cleared by the WebSocket handler on connect
    creds = {
        "password": body.get("ssh_password", ""),
        "gateway": body.get("gateway"),
    }

    sess = SSHSession(
        user=user["username"],
        asset_id=str(body.get("asset_id", "")),
        asset_name=body.get("asset_name", asset_address),
        asset_address=asset_address,
        ssh_username=ssh_username,
        status="pending",
        recording=creds,  # temporarily store creds
    )
    db.add(sess)
    await db.flush()
    await db.commit()

    log.info(
        "ssh_session_created", session_id=str(sess.id), host=asset_address, user=user["username"]
    )

    return {
        "session_id": str(sess.id),
        "ws_url": f"/ws/ssh/{sess.id}",
        "asset_address": asset_address,
        "ssh_username": ssh_username,
    }


@router.get("/sessions")
async def list_sessions(
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> dict:
    q = select(SSHSession)
    if status_filter:
        q = q.where(SSHSession.status == status_filter)

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    q = q.order_by(SSHSession.started_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    sessions = result.scalars().all()

    return {"total": total, "items": [_to_resp(s) for s in sessions]}


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> dict:
    s = await db.get(SSHSession, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return _to_resp(s)


@router.get("/sessions/{session_id}/replay")
async def get_session_replay(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> dict:
    """Return asciinema-compatible recording for replay."""
    s = await db.get(SSHSession, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    if s.status == "pending":
        raise HTTPException(status_code=409, detail="Session not yet started")

    return {
        "session_id": str(session_id),
        "asset_address": s.asset_address,
        "ssh_username": s.ssh_username,
        "user": s.user,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "duration_seconds": s.duration_seconds or 0,
        "frames": s.recording or [],
    }


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def terminate_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> None:
    s = await db.get(SSHSession, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    from ..ws.ssh_terminal import terminate_session as _terminate

    _terminate(str(session_id))

    if s.status == "active":
        s.status = "closed"
        s.exit_reason = f"Terminated by {user['username']}"
        await db.commit()
