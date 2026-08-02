"""Internal endpoints — called by zabbix-integration-service via service token."""

from __future__ import annotations

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app._params import ParamNotAllowedError, filter_caller_params, template_code_params
from app._production_gate import any_production_hosts
from app.database import get_session
from app.deps import require_service_token
from app.models import ZAJobRun, ZAJobTemplate

router = APIRouter(dependencies=[Depends(require_service_token)])


class InternalRunCreate(BaseModel):
    job_template_id: str
    triggered_by: str
    params: dict = {}
    target_host_ids: list[str] = []


@router.post("/internal/job-runs", status_code=status.HTTP_202_ACCEPTED)
async def create_run_internal(
    payload: InternalRunCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    tmpl = await session.get(ZAJobTemplate, payload.job_template_id)
    if tmpl is None:
        raise HTTPException(status_code=404, detail="job template not found")

    run_id = str(uuid.uuid4())
    target_host_ids = payload.target_host_ids or list(tmpl.target_host_ids or [])
    # Webhook payloads are attacker-influenceable; allowlist caller params and take
    # script_content/playbook_path from the template ONLY (closes the injection path).
    try:
        caller_params = filter_caller_params(tmpl, payload.params)
    except ParamNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    params = {**tmpl.default_params, **caller_params, **template_code_params(tmpl)}

    # PCI DSS Phase D — production is gated regardless of trigger mechanism; a
    # Zabbix-triggered run targeting a production host is not exempt just
    # because a human didn't click "Run". See app/_production_gate.py.
    force_approval = await any_production_hosts(target_host_ids)
    run = ZAJobRun(
        id=run_id,
        job_template_id=payload.job_template_id,
        triggered_by=payload.triggered_by,
        status="pending_approval" if force_approval else "pending",
        params=params,
        output_lines=[],
    )
    session.add(run)
    await session.commit()

    if force_approval:
        return {"run_id": run_id, "status": "pending_approval"}

    executors = getattr(request.app.state, "executors", {})
    executor = executors.get(tmpl.action_type)
    if executor:
        from libs.pluginbase import RunRequest
        from app import runner as _runner

        req = RunRequest(
            action_type=tmpl.action_type,
            target_host_ids=target_host_ids,
            credential_id=tmpl.credential_id,
            params=params,
            triggered_by=payload.triggered_by,
        )
        asyncio.create_task(_runner.execute(run_id, executor, req))

    return {"run_id": run_id}


class InternalNotificationCreate(BaseModel):
    user_id: str | None = None
    severity: Literal["info", "medium", "critical"] = "info"
    title: str = Field(max_length=300)
    message: str = Field(default="", max_length=2000)
    source_type: str = Field(default="external", max_length=30)
    source_id: str | None = Field(default=None, max_length=36)


@router.post("/internal/notifications", status_code=status.HTTP_201_CREATED)
async def create_notification_internal(payload: InternalNotificationCreate):
    """PCI DSS Phase B: lets other services raise a real in-app + emailed alert
    without touching mail credentials or notification plumbing themselves — e.g.
    inventory-service's rotation-due sweep, identity-service's security-event
    alerts (privileged role grants, audit-chain write failures, login spikes).

    Unlike its sibling internal endpoints, this one's content (title/message)
    renders directly in the caller's notification UI — validated to the same
    field shapes ZANotification's model enforces (Literal severity, bounded
    lengths) rather than accepting arbitrary strings, even though the caller
    is already a trusted service-token holder."""
    from app.runner import create_notification

    notif = await create_notification(
        user_id=payload.user_id, severity=payload.severity, title=payload.title,
        message=payload.message, source_type=payload.source_type, source_id=payload.source_id,
    )
    return {"id": notif.id}


@router.get("/internal/job-runs/{run_id}")
async def get_run_internal(run_id: str, session: AsyncSession = Depends(get_session)):
    run = await session.get(ZAJobRun, run_id)
    if run is None:
        raise HTTPException(status_code=404)
    return {
        "id": run.id, "status": run.status, "exit_code": run.exit_code,
        "triggered_by": run.triggered_by,
        "output_lines": run.output_lines or [],
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
    }
