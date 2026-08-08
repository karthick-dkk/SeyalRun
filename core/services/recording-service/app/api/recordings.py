"""Public recordings API — listing, detail, frame retrieval for playback."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.servicetoken import mint

from ..config import get_settings
from ..database import get_session
from ..deps import current_user_id, current_user_role, require_service_token
from ..models import ZARecording

logger = logging.getLogger(__name__)

router = APIRouter(tags=["recordings"], dependencies=[Depends(require_service_token)])


class RecordingOut(BaseModel):
    id: str
    session_id: str
    format: str
    duration_seconds: float
    size_bytes: int
    created_at: datetime
    session_info: dict | None = None

    model_config = {"from_attributes": True}


async def _get_authorized_host_ids(user_id: str, settings) -> list[str] | None:
    """Returns list of authorized host_ids (None = admin, all allowed)."""
    token = mint("recording-service", "identity-service", settings.service_jwt_secret)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{settings.identity_service_url}/api/v1/internal/authz/host-ids",
                headers={"X-Service-Token": token},
                params={"user_id": user_id},
            )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("is_admin"):
                return None  # admin sees all
            return data.get("host_ids", [])
    except httpx.HTTPError:
        pass
    return []


async def _get_session_info(session_id: str, settings) -> dict | None:
    token = mint("recording-service", "terminal-service", settings.service_jwt_secret)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{settings.terminal_service_url}/api/v1/internal/sessions/{session_id}",
                headers={"X-Service-Token": token},
            )
        if resp.status_code == 200:
            return resp.json()
    except httpx.HTTPError:
        pass
    return None


@router.get("/recordings", response_model=list[RecordingOut])
async def list_recordings(
    host_id: str | None = None,
    user_id_filter: str | None = None,
    session_id: str | None = None,
    user_id: str = Depends(current_user_id),
    role: str = Depends(current_user_role),
    db: AsyncSession = Depends(get_session),
):
    settings = get_settings()
    is_admin = role in ("admin", "superadmin")

    authorized_host_ids: list[str] | None = None
    if not is_admin:
        authorized_host_ids = await _get_authorized_host_ids(user_id, settings)

    stmt = select(ZARecording).order_by(ZARecording.created_at.desc())
    if session_id:
        stmt = stmt.where(ZARecording.session_id == session_id)

    result = await db.execute(stmt)
    rows = result.scalars().all()

    out = []
    for r in rows:
        sess_info = await _get_session_info(r.session_id, settings)
        if sess_info:
            if not is_admin and authorized_host_ids is not None:
                if sess_info.get("host_id") not in authorized_host_ids:
                    continue
            if host_id and sess_info.get("host_id") != host_id:
                continue
            if user_id_filter and not is_admin and sess_info.get("user_id") != user_id_filter:
                continue
            if not is_admin and sess_info.get("user_id") != user_id and authorized_host_ids is None:
                continue

        rec = RecordingOut.model_validate(r)
        rec.session_info = sess_info
        out.append(rec)
    return out


@router.get("/recordings/{recording_id}", response_model=RecordingOut)
async def get_recording(
    recording_id: str,
    user_id: str = Depends(current_user_id),
    role: str = Depends(current_user_role),
    db: AsyncSession = Depends(get_session),
):
    settings = get_settings()
    result = await db.execute(select(ZARecording).where(ZARecording.id == recording_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="recording not found")

    sess_info = await _get_session_info(row.session_id, settings)
    is_admin = role in ("admin", "superadmin")
    if not is_admin and sess_info:
        if sess_info.get("user_id") != user_id:
            authorized_host_ids = await _get_authorized_host_ids(user_id, settings)
            if authorized_host_ids is not None and sess_info.get("host_id") not in authorized_host_ids:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    rec = RecordingOut.model_validate(row)
    rec.session_info = sess_info
    return rec


@router.get("/recordings/{recording_id}/frames")
async def get_recording_frames(
    recording_id: str,
    user_id: str = Depends(current_user_id),
    role: str = Depends(current_user_role),
    db: AsyncSession = Depends(get_session),
):
    settings = get_settings()
    result = await db.execute(select(ZARecording).where(ZARecording.id == recording_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="recording not found")

    sess_info = await _get_session_info(row.session_id, settings)
    is_admin = role in ("admin", "superadmin")
    if not is_admin and sess_info and sess_info.get("user_id") != user_id:
        authorized_host_ids = await _get_authorized_host_ids(user_id, settings)
        if authorized_host_ids is not None and sess_info.get("host_id") not in authorized_host_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    # Tiered to S3 (Feature 4): fetch frames back transparently for playback.
    if getattr(row, "storage_location", "local") == "s3" and row.storage_key:
        from .. import storage
        try:
            frames = await storage.read_from_s3(row.storage_key, settings)
            return {"frames": frames, "duration_seconds": row.duration_seconds}
        except Exception:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="tiered recording unavailable")

    frames = row.frames if isinstance(row.frames, list) else json.loads(row.frames or "[]")
    return {"frames": frames, "duration_seconds": row.duration_seconds}
