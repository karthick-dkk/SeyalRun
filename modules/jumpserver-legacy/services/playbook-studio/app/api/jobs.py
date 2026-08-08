"""Job execution API — run playbooks, stream output, cancel."""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_async_session
from ..dependencies import get_current_user
from ..models.domain import Job, Playbook
from ..models.schemas import JobCreate, JobListResponse, JobResponse

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _to_response(j: Job) -> JobResponse:
    return JobResponse(
        id=j.id,
        playbook_id=j.playbook_id,
        status=j.status,
        triggered_by=j.triggered_by,
        ab_job_id=j.ab_job_id,
        inventory_selector=j.inventory_selector,
        extra_vars=j.extra_vars or {},
        started_at=j.started_at,
        finished_at=j.finished_at,
        duration_seconds=j.duration_seconds,
        exit_code=j.exit_code,
        output_lines=j.output_lines or [],
        created_at=j.created_at,
    )


@router.get("", response_model=JobListResponse)
async def list_jobs(
    playbook_id: uuid.UUID | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> JobListResponse:
    q = select(Job)
    if playbook_id:
        q = q.where(Job.playbook_id == playbook_id)
    if status_filter:
        q = q.where(Job.status == status_filter)

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    q = q.order_by(Job.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    jobs = result.scalars().all()

    return JobListResponse(total=total, items=[_to_response(j) for j in jobs])


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def execute_job(
    body: JobCreate,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> JobResponse:
    """Execute a playbook. Returns immediately; use /ws/jobs/{id}/stream for live output."""
    p = await db.get(Playbook, body.playbook_id)
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playbook not found")

    if not p.yaml_cache:
        from ..models.schemas import TaskDefinition, VariableDefinition
        from ..services.playbook_builder import build_yaml

        tasks = [TaskDefinition(**t) if isinstance(t, dict) else t for t in (p.tasks or [])]
        variables = [
            VariableDefinition(**v) if isinstance(v, dict) else v for v in (p.variables or [])
        ]
        p.yaml_cache = build_yaml(playbook_name=p.name, tasks=tasks, variables=variables)

    j = Job(
        playbook_id=p.id,
        status="pending",
        triggered_by=user["username"],
        inventory_selector=body.inventory_selector,
        extra_vars=body.extra_vars or {},
        yaml_content=p.yaml_cache,
    )
    db.add(j)
    await db.flush()

    log.info("job_queued", job_id=str(j.id), playbook=p.name, user=user["username"])

    # Launch async execution in background
    import asyncio

    from ..services.execution import run_job_async

    asyncio.create_task(run_job_async(str(j.id)))

    return _to_response(j)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> JobResponse:
    j = await db.get(Job, job_id)
    if not j:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return _to_response(j)


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> JobResponse:
    j = await db.get(Job, job_id)
    if not j:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if j.status not in ("pending", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel job in '{j.status}' state",
        )

    j.status = "cancelled"
    await db.flush()

    import asyncio

    from ..services.execution import cancel_job_async

    asyncio.create_task(cancel_job_async(str(job_id), j.ab_job_id))

    log.info("job_cancelled", job_id=str(job_id), user=user["username"])
    return _to_response(j)
