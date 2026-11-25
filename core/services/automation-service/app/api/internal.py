"""Internal endpoints — called by zabbix-integration-service, inventory-service and
posture-service via service token."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app._attribution import ACCOUNTABLE_PARAM, accountable_ids
from app._concurrency import LIVE_STATUSES
from app._params import ParamNotAllowedError, filter_caller_params, template_code_params
from app import audit
from app._host_ops import (
    APPROVED_BY_PARAM,
    HOST_OP_PARAM,
    NO_RERUN_ACTIONS,
    OP_TOKEN_PARAM,
    READ_ONLY_OPS,
    HostOpError,
    argv_for,
    check_token,
)
from app._posture import (
    POSTURE_ACTION,
    earlier_approver,
    ensure_host_op_template,
    ensure_posture_template,
    host_op_runs,
    mutating_host_ops,
    tunable_params,
)
from app._production_gate import any_production_hosts
from app.database import get_session
from app.deps import require_service_token
from app.models import ZAJobRun, ZAJobTemplate
from app.runner import LIVE_RUNS

router = APIRouter(dependencies=[Depends(require_service_token)])


class InternalRunCreate(BaseModel):
    job_template_id: str
    triggered_by: str
    params: dict = {}
    target_host_ids: list[str] = []
    # People who configured the trigger (e.g. a Zabbix binding's author/editor):
    # they may not approve the run it produces (F-2). Service-asserted only.
    accountable_user_ids: list[str] = []


@router.post("/internal/job-runs", status_code=status.HTTP_202_ACCEPTED)
async def create_run_internal(
    payload: InternalRunCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    tmpl = await session.get(ZAJobTemplate, payload.job_template_id)
    if tmpl is None:
        raise HTTPException(status_code=404, detail="job template not found")
    if tmpl.action_type in NO_RERUN_ACTIONS:
        raise HTTPException(status_code=400, detail="this template is run by Posture → Patching only")

    run_id = str(uuid.uuid4())
    target_host_ids = payload.target_host_ids or list(tmpl.target_host_ids or [])
    # Webhook payloads are attacker-influenceable; allowlist caller params and take
    # script_content/playbook_path from the template ONLY (closes the injection path).
    try:
        caller_params = filter_caller_params(tmpl, payload.params)
    except ParamNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    params = {**tmpl.default_params, **caller_params, **template_code_params(tmpl)}
    params[ACCOUNTABLE_PARAM] = accountable_ids(*payload.accountable_user_ids)

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


class PostureFactsRunCreate(BaseModel):
    host_ids: list[str] = Field(min_length=1, max_length=1000)
    # The person who asked posture-service for the collection — the run is
    # attributed to them (job.start/job.finish audit rows) and, on a production
    # host, they may not approve it themselves (F-2).
    requested_by: str = Field(min_length=1, max_length=36)
    approval: "HostOpApproval | None" = None


class HostOpApproval(BaseModel):
    """A rollout a second person approved, as posture-service recorded it. Accepted
    in place of the per-run production gate, so 300 hosts need one approval and not
    300 — but only for an approver who is not accountable for the change (F-2)."""

    reference: str = Field(min_length=1, max_length=80)
    approved_by: str = Field(min_length=1, max_length=36)
    accountable_user_ids: list[str] = Field(default_factory=list, max_length=200)


PostureFactsRunCreate.model_rebuild()


async def _production_gated(host_id: str, requested_by: str, approval: HostOpApproval | None,
                            *, action: str, run_id: str) -> bool:
    """True when this run must wait for a per-run approval."""
    if not await any_production_hosts([host_id]):
        return False
    if approval is None:
        return True
    if approval.approved_by in {requested_by, *approval.accountable_user_ids}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="the approver is accountable for this change and cannot approve it")
    try:
        await audit.log_action(
            user_id=approval.approved_by, action="job.approve", resource_type="job_run", resource_id=run_id,
            details={"via": "patch_rollout", "reference": approval.reference, "requested_by": requested_by,
                     "host_id": host_id, "action_type": action},
            result="success", critical=True)
    except audit.AuditWriteError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="approval could not be recorded in the audit log; the run was not started") from exc
    return False


@router.post("/internal/posture/facts-runs", status_code=status.HTTP_202_ACCEPTED)
async def create_posture_facts_runs(
    payload: PostureFactsRunCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """One read-only facts run per host for posture-service.

    The caller names hosts, never a script: the posture_facts executor runs a
    file fixed in this image. A host that already has a live facts run gets that
    run back instead of a second one (a double-clicked "collect all" must not
    queue duplicate SSH sessions). Production hosts go through the approval gate
    exactly like any other job — read-only is not an exemption from PCI change
    control, and widening it here would be a silent one."""
    executor = getattr(request.app.state, "executors", {}).get(POSTURE_ACTION)
    if executor is None:
        raise HTTPException(status_code=503, detail="posture_facts executor is not loaded")
    tmpl = await ensure_posture_template(session)

    live: dict[str, ZAJobRun] = {}
    res = await session.execute(
        select(ZAJobRun).where(ZAJobRun.job_template_id == tmpl.id, ZAJobRun.status.in_(LIVE_STATUSES))
    )
    for run in res.scalars().all():
        for hid in (run.params or {}).get("_target_host_ids") or []:
            if run.status == "pending_approval":
                if payload.approval is not None and hid in payload.host_ids:
                    _supersede(run, f"superseded by {payload.approval.reference}")
                    continue
            elif run.id not in LIVE_RUNS:
                continue        # its task is gone; reusing it would wait for nothing
            live[hid] = run

    from libs.pluginbase import RunRequest
    from app import runner as _runner

    triggered_by = f"user:{payload.requested_by}"
    out, dispatch = [], []
    for host_id in dict.fromkeys(payload.host_ids):  # de-dupe, keep order
        if host_id in live:
            out.append({"host_id": host_id, "run_id": live[host_id].id, "status": live[host_id].status,
                        "reused": True})
            continue
        run_id = str(uuid.uuid4())
        params = {
            **tunable_params(tmpl),
            "_target_host_ids": [host_id],
            "_credential_id": None,
            "_host_credentials": {},
            "_max_parallel": 1,
            "_run_id": run_id,
            ACCOUNTABLE_PARAM: accountable_ids(payload.requested_by),
        }
        gated = await _production_gated(host_id, payload.requested_by, payload.approval,
                                        action=POSTURE_ACTION, run_id=run_id)
        session.add(ZAJobRun(
            id=run_id, job_template_id=tmpl.id, triggered_by=triggered_by,
            status="pending_approval" if gated else "pending", params=params, output_lines=[],
        ))
        out.append({"host_id": host_id, "run_id": run_id,
                    "status": "pending_approval" if gated else "pending", "reused": False})
        if not gated:
            dispatch.append((run_id, RunRequest(
                action_type=POSTURE_ACTION, target_host_ids=[host_id], credential_id=None,
                params=params, triggered_by=triggered_by,
            )))
    await session.commit()

    for run_id, req in dispatch:
        LIVE_RUNS.add(run_id)
        asyncio.create_task(_runner.execute(
            run_id, executor, req, timeout_seconds=tmpl.timeout_seconds, template_name=tmpl.name,
        ))
    return {"runs": out}


@router.get("/internal/job-runs/{run_id}")
async def get_run_internal(run_id: str, session: AsyncSession = Depends(get_session)):
    run = await session.get(ZAJobRun, run_id)
    if run is None:
        raise HTTPException(status_code=404)
    return {
        "id": run.id, "status": run.status, "exit_code": run.exit_code,
        "triggered_by": run.triggered_by,
        "output_lines": run.output_lines or [],
        "live": run.id in LIVE_RUNS,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
    }


class HostOpRunCreate(BaseModel):
    action: Literal["package_ops", "host_reboot"]
    op: str = Field(min_length=1, max_length=20)
    args: dict = Field(default_factory=dict)
    host_id: str = Field(min_length=1, max_length=36)
    # Names the operation ON THE HOST: the same token re-attaches, never repeats.
    token: str = Field(min_length=8, max_length=80)
    requested_by: str = Field(min_length=1, max_length=36)
    approval: HostOpApproval | None = None
    # Editors of the mirrors this operation installs from: they may not approve it (F-2).
    accountable_user_ids: list[str] = Field(default_factory=list, max_length=200)


def _supersede(run: ZAJobRun, why: str) -> None:
    run.status, run.ended_at = "cancelled", datetime.now(timezone.utc)
    run.output_lines = [*(run.output_lines or []), f"[cancelled] {why}"]


@router.post("/internal/posture/host-runs", status_code=status.HTTP_202_ACCEPTED)
async def create_host_op_run(payload: HostOpRunCreate, request: Request, session: AsyncSession = Depends(get_session)):
    """One validated package or reboot operation on one host for posture-service."""
    executor = getattr(request.app.state, "executors", {}).get(payload.action)
    if executor is None:
        raise HTTPException(status_code=503, detail=f"{payload.action} executor is not loaded")
    try:
        check_token(payload.token)
        argv_for(payload.action, payload.op, payload.args)
    except (HostOpError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    tmpl = await ensure_host_op_template(session, payload.action)
    if not tmpl.enabled:
        raise HTTPException(status_code=409, detail=f"template {tmpl.name!r} is disabled")

    for run in await host_op_runs(session, payload.host_id):
        op = ((run.params or {}).get(HOST_OP_PARAM) or {}).get("op")
        if (run.params or {}).get(OP_TOKEN_PARAM) == payload.token:
            if run.id in LIVE_RUNS or run.status == "pending_approval":
                return {"run_id": run.id, "status": run.status, "reused": True}
            # Left behind by a restart: the new run re-attaches to it on the host.
            _supersede(run, "interrupted; continued in a new run")
        elif (payload.approval is not None and run.status == "pending_approval"
              and payload.op not in READ_ONLY_OPS and op not in READ_ONLY_OPS):
            # An unapproved request must not block, or later run inside, an approved rollout.
            _supersede(run, f"superseded by {payload.approval.reference}; request it again afterwards")
    if payload.op not in READ_ONLY_OPS and await mutating_host_ops(session, payload.host_id, token=payload.token):
        await session.commit()
        raise HTTPException(status_code=409, detail="another package or reboot operation is active on this host")

    run_id = str(uuid.uuid4())
    accountable = accountable_ids(payload.requested_by, *payload.accountable_user_ids,
                                  *(payload.approval.accountable_user_ids if payload.approval else []))
    host_op = {"op": payload.op, "args": payload.args}
    approver = await earlier_approver(session, tmpl.id, payload.host_id, payload.token, host_op)
    gated = approver is None and await _production_gated(
        payload.host_id, payload.requested_by, payload.approval, action=f"{payload.action}:{payload.op}", run_id=run_id)
    params = {
        **tunable_params(tmpl), "_target_host_ids": [payload.host_id], "_credential_id": None,
        "_host_credentials": {}, "_run_id": run_id, OP_TOKEN_PARAM: payload.token,
        HOST_OP_PARAM: host_op, ACCOUNTABLE_PARAM: accountable,
    }
    if approver:
        params[APPROVED_BY_PARAM] = approver
    triggered_by = f"user:{payload.requested_by}"
    session.add(ZAJobRun(id=run_id, job_template_id=tmpl.id, triggered_by=triggered_by,
                         status="pending_approval" if gated else "pending", params=params, output_lines=[]))
    await session.commit()
    if gated:
        return {"run_id": run_id, "status": "pending_approval", "reused": False}

    from libs.pluginbase import RunRequest

    req = RunRequest(action_type=payload.action, target_host_ids=[payload.host_id], credential_id=None,
                     params=params, triggered_by=triggered_by)
    _dispatch_host_op(run_id, executor, req, tmpl)
    return {"run_id": run_id, "status": "pending", "reused": False}


def _dispatch_host_op(run_id: str, executor, req, tmpl: ZAJobTemplate) -> None:
    from app import runner as _runner

    LIVE_RUNS.add(run_id)       # before the first poll can see it
    task = asyncio.create_task(_runner.execute(run_id, executor, req, timeout_seconds=tmpl.timeout_seconds,
                                               template_name=tmpl.name))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


_TASKS: set[asyncio.Task] = set()     # asyncio keeps only weak references to tasks


class HookRunCreate(BaseModel):
    job_template_id: str = Field(min_length=1, max_length=36)
    # The template as it was when the rollout was approved. A hook edited since then
    # is not the hook that was approved.
    expect_updated_at: str = Field(min_length=1, max_length=40)
    host_id: str = Field(min_length=1, max_length=36)
    requested_by: str = Field(min_length=1, max_length=36)
    # None only for a rollout that needed no approval; a production host is then gated per run.
    approval: HostOpApproval | None = None


_HOOK_ACTIONS = frozenset({"bash_script", "ansible_playbook"})


@router.get("/internal/posture/hook-templates/{template_id}")
async def get_hook_template(template_id: str, session: AsyncSession = Depends(get_session)):
    tmpl = await session.get(ZAJobTemplate, template_id)
    if tmpl is None or tmpl.action_type not in _HOOK_ACTIONS:
        raise HTTPException(status_code=404, detail="no such bash or ansible template")
    return {"id": tmpl.id, "name": tmpl.name, "action_type": tmpl.action_type, "enabled": tmpl.enabled,
            "requires_approval": tmpl.requires_approval, "editor_ids": accountable_ids(tmpl.created_by, *(tmpl.editor_ids or [])),
            "updated_at": tmpl.updated_at.isoformat() if tmpl.updated_at else ""}


@router.post("/internal/posture/hook-runs", status_code=status.HTTP_202_ACCEPTED)
async def create_hook_run(payload: HookRunCreate, request: Request, session: AsyncSession = Depends(get_session)):
    """A rollout's drain/undrain hook: an existing template, on one host, as approved."""
    tmpl = await session.get(ZAJobTemplate, payload.job_template_id)
    if tmpl is None or tmpl.action_type not in _HOOK_ACTIONS:
        raise HTTPException(status_code=404, detail="no such bash or ansible template")
    if not tmpl.enabled:
        raise HTTPException(status_code=409, detail=f"hook template {tmpl.name!r} is disabled")
    if (tmpl.updated_at.isoformat() if tmpl.updated_at else "") != payload.expect_updated_at:
        raise HTTPException(status_code=409, detail=f"hook template {tmpl.name!r} changed after the rollout was approved")
    executor = getattr(request.app.state, "executors", {}).get(tmpl.action_type)
    if executor is None:
        raise HTTPException(status_code=503, detail=f"{tmpl.action_type} executor is not loaded")
    run_id = str(uuid.uuid4())
    gated = await _production_gated(payload.host_id, payload.requested_by, payload.approval,
                                    action=f"hook:{tmpl.action_type}", run_id=run_id)
    params = {**(tmpl.default_params or {}), **template_code_params(tmpl), "run_local": False,
              "_target_host_ids": [payload.host_id], "_credential_id": tmpl.credential_id, "_host_credentials": {},
              "_max_parallel": 1, "_run_id": run_id,
              ACCOUNTABLE_PARAM: accountable_ids(
                  payload.requested_by, *(payload.approval.accountable_user_ids if payload.approval else []))}
    triggered_by = f"user:{payload.requested_by}"
    session.add(ZAJobRun(id=run_id, job_template_id=tmpl.id, triggered_by=triggered_by,
                         status="pending_approval" if gated else "pending", params=params, output_lines=[]))
    await session.commit()
    if not gated:
        from libs.pluginbase import RunRequest

        _dispatch_host_op(run_id, executor, RunRequest(
            action_type=tmpl.action_type, target_host_ids=[payload.host_id], credential_id=tmpl.credential_id,
            params=params, triggered_by=triggered_by), tmpl)
    return {"run_id": run_id, "status": "pending_approval" if gated else "pending"}
