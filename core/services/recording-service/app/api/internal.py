"""Internal API — called by terminal-service to write recordings after session close."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_session
from ..deps import require_service_token
from ..models import ZARecording
from .. import storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(require_service_token)])


class RecordingWrite(BaseModel):
    session_id: str
    frames: list[dict]
    duration_seconds: float


@router.post("/recordings", status_code=status.HTTP_201_CREATED)
async def write_recording(payload: RecordingWrite, db: AsyncSession = Depends(get_session)):
    """Upsert recording for a session (terminal-service posts this on session close)."""
    result = await db.execute(select(ZARecording).where(ZARecording.session_id == payload.session_id))
    existing = result.scalar_one_or_none()

    frames_json = payload.frames
    size_bytes = len(json.dumps(frames_json))

    if existing:
        existing.frames = frames_json
        existing.duration_seconds = payload.duration_seconds
        existing.size_bytes = size_bytes
        existing.format = "frames_v1"
        await db.commit()
        return {"id": existing.id, "created": False}
    else:
        rec = ZARecording(
            id=str(uuid.uuid4()),
            session_id=payload.session_id,
            frames=frames_json,
            duration_seconds=payload.duration_seconds,
            size_bytes=size_bytes,
        )
        db.add(rec)
        await db.commit()
        return {"id": rec.id, "created": True}


@router.post("/housekeeping/purge")
async def housekeeping_purge(db: AsyncSession = Depends(get_session)):
    """Purge frame data from recordings older than RECORDING_RETENTION_DAYS (keeps the audit row)."""
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.recording_retention_days)
    result = await db.execute(select(ZARecording).where(ZARecording.created_at < cutoff))
    purged = 0
    for rec in result.scalars().all():
        if rec.format == "purged":
            continue
        rec.frames = []
        rec.format = "purged"
        rec.size_bytes = 0
        purged += 1
    await db.commit()
    return {"purged": purged}


@router.post("/housekeeping/tier")
async def housekeeping_tier(db: AsyncSession = Depends(get_session)):
    """Move local recordings older than RECORDING_TIER_AFTER_DAYS to S3 (no-op if S3 unconfigured)."""
    settings = get_settings()
    s3_target = await storage.resolve_recording_s3(settings)
    if s3_target is None:
        return {"tiered": 0, "skipped": "recordings not routed to s3"}
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.recording_tier_after_days)
    result = await db.execute(
        select(ZARecording).where(
            ZARecording.created_at < cutoff,
            ZARecording.storage_location == "local",
            ZARecording.format == "frames_v1",
        )
    )
    tiered = 0
    for rec in result.scalars().all():
        if not rec.frames:
            continue
        key = await storage.write_to_s3(rec.id, rec.frames, s3_target)
        rec.storage_location = "s3"
        rec.storage_key = key
        rec.tiered_at = datetime.now(timezone.utc)
        rec.frames = []
        tiered += 1
    await db.commit()
    return {"tiered": tiered}
