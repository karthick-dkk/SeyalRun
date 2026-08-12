"""Admin API for housekeeping jobs (v1.1 Feature 3)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import get_user_role, require_service_token
from ..models import ZAHousekeepingJob

router = APIRouter(prefix="/housekeeping", tags=["housekeeping"], dependencies=[Depends(require_service_token)])


def _require_admin(role: str = Depends(get_user_role)) -> None:
    if role not in ("admin", "superadmin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin role required")


def _out(j: ZAHousekeepingJob) -> dict:
    return {
        "id": j.id, "job_key": j.job_key, "display_name": j.display_name, "description": j.description,
        "enabled": j.enabled, "cron_expression": j.cron_expression, "cron_override": j.cron_override,
        "last_run_at": j.last_run_at.isoformat() if j.last_run_at else None,
        "last_run_status": j.last_run_status, "last_run_error": j.last_run_error,
        "next_run_at": j.next_run_at.isoformat() if j.next_run_at else None,
        "manual_trigger": j.manual_trigger,
    }


@router.get("/jobs", dependencies=[Depends(_require_admin)])
async def list_jobs(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAHousekeepingJob).order_by(ZAHousekeepingJob.display_name))
    return [_out(j) for j in result.scalars().all()]


class JobUpdate(BaseModel):
    enabled: bool | None = None
    cron_override: str | None = None


@router.put("/jobs/{job_key}", dependencies=[Depends(_require_admin)])
async def update_job(job_key: str, payload: JobUpdate, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAHousekeepingJob).where(ZAHousekeepingJob.job_key == job_key))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    if payload.enabled is not None:
        job.enabled = payload.enabled
    if payload.cron_override is not None:
        job.cron_override = payload.cron_override or None
        job.next_run_at = None  # recompute on next tick from the new cron
    await session.commit()
    await session.refresh(job)
    return _out(job)


@router.post("/jobs/{job_key}/trigger", dependencies=[Depends(_require_admin)])
async def trigger_job(job_key: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAHousekeepingJob).where(ZAHousekeepingJob.job_key == job_key))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    job.manual_trigger = True
    await session.commit()
    return {"job_key": job_key, "queued": True}
