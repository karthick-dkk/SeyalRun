from __future__ import annotations

from datetime import datetime, timezone

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.deps import require_service_token, get_user_role
from app.models import ZASchedule

router = APIRouter(dependencies=[Depends(require_service_token)])


class ScheduleCreate(BaseModel):
    job_template_id: str
    name: str
    cron_expression: str
    params_override: dict = {}
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    name: str | None = None
    cron_expression: str | None = None
    params_override: dict | None = None
    enabled: bool | None = None


@router.get("/schedules")
async def list_schedules(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZASchedule).order_by(ZASchedule.created_at.desc()))
    return [_out(s) for s in result.scalars().all()]


@router.post("/schedules", status_code=status.HTTP_201_CREATED)
async def create_schedule(
    payload: ScheduleCreate,
    role: str = Depends(get_user_role),
    session: AsyncSession = Depends(get_session),
):
    if role not in ("superadmin", "admin", "automation"):
        raise HTTPException(status_code=403, detail="automation write access required")
    if not croniter.is_valid(payload.cron_expression):
        raise HTTPException(status_code=400, detail="invalid cron_expression")
    now = datetime.now(timezone.utc)
    ci = croniter(payload.cron_expression, now)
    sched = ZASchedule(**payload.model_dump(), next_run_at=ci.get_next(datetime))
    session.add(sched)
    await session.commit()
    await session.refresh(sched)
    return _out(sched)


@router.get("/schedules/{schedule_id}")
async def get_schedule(schedule_id: str, session: AsyncSession = Depends(get_session)):
    s = await session.get(ZASchedule, schedule_id)
    if s is None:
        raise HTTPException(status_code=404)
    return _out(s)


@router.put("/schedules/{schedule_id}")
async def update_schedule(
    schedule_id: str,
    payload: ScheduleUpdate,
    role: str = Depends(get_user_role),
    session: AsyncSession = Depends(get_session),
):
    if role not in ("superadmin", "admin", "automation"):
        raise HTTPException(status_code=403, detail="automation write access required")
    s = await session.get(ZASchedule, schedule_id)
    if s is None:
        raise HTTPException(status_code=404)
    if payload.cron_expression and not croniter.is_valid(payload.cron_expression):
        raise HTTPException(status_code=400, detail="invalid cron_expression")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(s, k, v)
    if payload.cron_expression:
        ci = croniter(payload.cron_expression, datetime.now(timezone.utc))
        s.next_run_at = ci.get_next(datetime)
    await session.commit()
    await session.refresh(s)
    return _out(s)


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: str,
    role: str = Depends(get_user_role),
    session: AsyncSession = Depends(get_session),
):
    if role not in ("superadmin", "admin", "automation"):
        raise HTTPException(status_code=403, detail="automation write access required")
    s = await session.get(ZASchedule, schedule_id)
    if s is None:
        raise HTTPException(status_code=404)
    await session.delete(s)
    await session.commit()


def _out(s: ZASchedule) -> dict:
    return {
        "id": s.id, "job_template_id": s.job_template_id, "name": s.name,
        "cron_expression": s.cron_expression, "params_override": s.params_override,
        "enabled": s.enabled,
        "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
        "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }
