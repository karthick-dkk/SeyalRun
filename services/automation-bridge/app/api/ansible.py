"""Ansible job API — POST /run/ansible, GET /jobs/{id}."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..config import Settings, get_settings
from ..models import (
    AnsibleRunRequest,
    ErrorResponse,
    JobResponse,
    JobStatus,
    JobStatusResponse,
)
from ..runners.ansible_runner import AnsibleJobRunner

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/run", tags=["ansible"])

# Job concurrency semaphore (set during app startup)
_job_semaphore: asyncio.Semaphore | None = None
_redis_client: aioredis.Redis | None = None


def get_semaphore() -> asyncio.Semaphore:
    if _job_semaphore is None:
        raise RuntimeError("Semaphore not initialized")
    return _job_semaphore


def get_redis() -> aioredis.Redis:
    if _redis_client is None:
        raise RuntimeError("Redis not initialized")
    return _redis_client


def init_resources(settings: Settings) -> None:
    global _job_semaphore, _redis_client
    _job_semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
    _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)


@router.post(
    "/ansible",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        400: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
)
async def run_ansible(
    request_body: AnsibleRunRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    semaphore: Annotated[asyncio.Semaphore, Depends(get_semaphore)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> JobResponse:
    """Submit an Ansible playbook job. Returns job_id for status polling."""

    # Validate playbook is in the approved list
    if request_body.playbook not in settings.approved_playbooks:
        log.warning(
            "unapproved_playbook_rejected",
            playbook=request_body.playbook,
            triggered_by=request_body.triggered_by,
            client_ip=request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Playbook '{request_body.playbook}' is not in the approved list.",
        )

    # Check concurrency limit before accepting the job
    if semaphore.locked() and semaphore._value == 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Max concurrent jobs ({settings.max_concurrent_jobs}) reached. Try later.",
        )

    job_id = uuid.uuid4()
    now = datetime.now(UTC).isoformat()

    job_data = {
        "job_id": str(job_id),
        "status": JobStatus.PENDING,
        "playbook": request_body.playbook,
        "triggered_by": request_body.triggered_by,
        "started_at": now,
        "finished_at": None,
        "exit_code": None,
        "output_lines": [],
    }
    await redis.setex(
        f"job:{job_id}",
        settings.job_ttl_seconds,
        json.dumps(job_data),
    )

    log.info(
        "ansible_job_accepted",
        job_id=str(job_id),
        playbook=request_body.playbook,
        triggered_by=request_body.triggered_by,
    )

    # Launch background task (does not block the response)
    asyncio.create_task(
        _execute_job(job_id, request_body, settings, semaphore, redis),
    )

    return JobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        playbook=request_body.playbook,
        triggered_by=request_body.triggered_by,
        message="Job accepted. Poll GET /jobs/{job_id} for status.",
    )


@router.get(
    "/ansible/jobs/{job_id}",
    response_model=JobStatusResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_job_status(
    job_id: uuid.UUID,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> JobStatusResponse:
    raw = await redis.get(f"job:{job_id}")
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found (may have expired).",
        )
    data = json.loads(raw)
    return JobStatusResponse(**data)


@router.post(
    "/ansible-inline",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={429: {"model": ErrorResponse}},
)
async def run_ansible_inline(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    semaphore: Annotated[asyncio.Semaphore, Depends(get_semaphore)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    yaml_content: str = "",
    inventory_selector: str = "all",
    extra_vars: dict | None = None,
    triggered_by: str = "playbook-studio",
    timeout_seconds: int = 600,
) -> JobResponse:
    """Execute inline YAML content from Playbook Studio (bypasses approved_playbooks whitelist)."""
    body = await request.json()
    yaml_content = body.get("yaml_content", "")
    inventory_selector = body.get("inventory_selector", "all")
    extra_vars = body.get("extra_vars", {})
    triggered_by = body.get("triggered_by", "playbook-studio")

    if not yaml_content.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="yaml_content is required",
        )

    if semaphore.locked() and semaphore._value == 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Max concurrent jobs ({settings.max_concurrent_jobs}) reached. Try later.",
        )

    job_id = uuid.uuid4()
    now = datetime.now(UTC).isoformat()

    # Write YAML to temp file in studio dir
    import os

    studio_dir = f"{settings.playbooks_dir}/studio"
    os.makedirs(studio_dir, exist_ok=True)
    yaml_path = f"{studio_dir}/{job_id}.yml"
    with open(yaml_path, "w") as f:
        f.write(yaml_content)

    job_data = {
        "job_id": str(job_id),
        "status": JobStatus.PENDING,
        "playbook": f"studio/{job_id}.yml",
        "triggered_by": triggered_by,
        "started_at": now,
        "finished_at": None,
        "exit_code": None,
        "output_lines": [],
    }
    await redis.setex(f"job:{job_id}", settings.job_ttl_seconds, json.dumps(job_data))

    log.info("ansible_inline_job_accepted", job_id=str(job_id), triggered_by=triggered_by)

    from ..models import AnsibleRunRequest

    inline_request = AnsibleRunRequest(
        playbook=f"studio/{job_id}.yml",
        inventory_selector=inventory_selector,
        extra_vars=extra_vars or {},
        triggered_by=triggered_by,
    )

    asyncio.create_task(
        _execute_inline_job(job_id, inline_request, yaml_path, settings, semaphore, redis),
    )

    return JobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        playbook=f"studio/{job_id}.yml",
        triggered_by=triggered_by,
        message="Inline job accepted. Poll GET /ansible/jobs/{job_id} for status.",
    )


async def _execute_inline_job(
    job_id: uuid.UUID,
    request_body: AnsibleRunRequest,
    yaml_path: str,
    settings: Settings,
    semaphore: asyncio.Semaphore,
    redis: aioredis.Redis,
) -> None:
    """Background task for inline jobs: run playbook, clean up temp file."""
    import os

    try:
        await _execute_job(job_id, request_body, settings, semaphore, redis)
    finally:
        try:
            os.unlink(yaml_path)
        except OSError:
            pass


async def _execute_job(
    job_id: uuid.UUID,
    request_body: AnsibleRunRequest,
    settings: Settings,
    semaphore: asyncio.Semaphore,
    redis: aioredis.Redis,
) -> None:
    """Background task: acquire semaphore, run job, update Redis."""
    async with semaphore:
        await redis.hset(f"job:{job_id}", "status", JobStatus.RUNNING)

        output_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        runner = AnsibleJobRunner(
            playbooks_dir=settings.playbooks_dir,
            timeout=settings.ansible_runner_timeout,
        )

        job_status, exit_code, all_lines = await runner.run(request_body, job_id, output_queue)

        now = datetime.now(UTC).isoformat()
        raw = await redis.get(f"job:{job_id}")
        if raw:
            data = json.loads(raw)
            data.update(
                {
                    "status": job_status,
                    "exit_code": exit_code,
                    "finished_at": now,
                    "output_lines": all_lines[-500:],  # Keep last 500 lines
                }
            )
            await redis.setex(f"job:{job_id}", settings.job_ttl_seconds, json.dumps(data))
