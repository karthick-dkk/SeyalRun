from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from sqlalchemy import text, update

from libs.obsmetrics import ServiceMetrics
from libs.securelog import configure_logging

from . import storage
from .config import get_settings
from .database import engine, SessionLocal
from .models import ZARecording
from .api.recordings import router as recordings_router
from .api.internal import router as internal_router

_settings = get_settings()
configure_logging("recording-service", _settings.log_level, _settings.log_path)
logger = logging.getLogger(__name__)


async def _retention_loop() -> None:
    """Hourly task: purge frame data for recordings older than RECORDING_RETENTION_DAYS."""
    settings = get_settings()
    while True:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=settings.recording_retention_days)
            async with SessionLocal() as db:
                await db.execute(
                    update(ZARecording)
                    .where(ZARecording.created_at < cutoff, ZARecording.format != "purged")
                    .values(frames=[], format="purged", size_bytes=0)
                )
                await db.commit()
        except Exception:
            logger.exception("retention purge error")
        await asyncio.sleep(3600)


async def _tier_loop() -> None:
    """Hourly task: move local recordings older than RECORDING_TIER_AFTER_DAYS to S3,
    but only when the admin routes the 'recording' category to S3 in the Log Backend
    config (resolved live from inventory-service). No routing → clean no-op."""
    settings = get_settings()
    while True:
        try:
            s3_target = await storage.resolve_recording_s3(settings)
            if s3_target is not None:
                cutoff = datetime.now(timezone.utc) - timedelta(days=settings.recording_tier_after_days)
                async with SessionLocal() as db:
                    from sqlalchemy import select
                    rows = (await db.execute(
                        select(ZARecording).where(
                            ZARecording.created_at < cutoff,
                            ZARecording.storage_location == "local",
                            ZARecording.format == "frames_v1",
                        )
                    )).scalars().all()
                    tiered = 0
                    for rec in rows:
                        if not rec.frames:
                            continue
                        rec.storage_key = await storage.write_to_s3(rec.id, rec.frames, s3_target)
                        rec.storage_location = "s3"
                        rec.tiered_at = datetime.now(timezone.utc)
                        rec.frames = []
                        tiered += 1
                    await db.commit()
                    if tiered:
                        logger.info("recording tier: moved %d recordings to S3", tiered)
        except Exception:
            logger.exception("recording tier loop error")
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    retention_task = asyncio.create_task(_retention_loop())
    tier_task = asyncio.create_task(_tier_loop())
    yield
    retention_task.cancel()
    tier_task.cancel()
    await engine.dispose()


app = FastAPI(title="recording-service", version="2.0.0", lifespan=lifespan)
_metrics = ServiceMetrics()
app.middleware("http")(_metrics.middleware)

app.include_router(recordings_router, prefix="/api/v1")
app.include_router(internal_router, prefix="/api/v1")


@app.get("/health")
async def health():
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "db": db_ok}


@app.get("/metrics")
async def metrics():
    return _metrics.snapshot()
