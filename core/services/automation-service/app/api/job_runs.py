from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app._params import template_code_params
from app.database import get_session, SessionLocal
from app.deps import require_service_token, get_user_id, get_user_role, get_user_real_role
from app.models import ZAJobRun, ZAJobTemplate
from app.config import get_settings
from app.runner import get_redis

_TERMINAL = {"success", "failed", "error", "cancelled", "rejected"}


def _can_approve(role: str, approver_role: str | None) -> bool:
    """'admin' (the default) means admin-or-above; 'superadmin' means superadmin only —
    the same two-tier admin/superadmin hierarchy every other admin-gated endpoint in
    this codebase already assumes (see housekeeping.py's _require_admin)."""
    required = approver_role or "admin"
    if required == "superadmin":
        return role == "superadmin"
    return role in ("admin", "superadmin")

router = APIRouter(dependencies=[Depends(require_service_token)])


@router.get("/job-runs")
async def list_runs(
    job_template_id: str | None = None,
    status: str | None = None,
    parent_run_id: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    q = select(ZAJobRun)
    if job_template_id:
        q = q.where(ZAJobRun.job_template_id == job_template_id)
    if status:
        q = q.where(ZAJobRun.status == status)
    if parent_run_id:
        q = q.where(ZAJobRun.parent_run_id == parent_run_id)
    q = q.order_by(ZAJobRun.started_at.desc()).limit(200)
    result = await session.execute(q)
    runs = result.scalars().all()
    # Batch-fetch the templates so each run can report its name / action / credential
    # without an N+1 query.
    tmpl_ids = {r.job_template_id for r in runs if r.job_template_id}
    tmpls: dict[str, ZAJobTemplate] = {}
    if tmpl_ids:
        tr = await session.execute(select(ZAJobTemplate).where(ZAJobTemplate.id.in_(tmpl_ids)))
        tmpls = {t.id: t for t in tr.scalars().all()}
    return [_out(r, tmpls.get(r.job_template_id)) for r in runs]


@router.get("/job-runs/{run_id}")
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)):
    run = await session.get(ZAJobRun, run_id)
    if run is None:
        raise HTTPException(status_code=404)
    tmpl = await session.get(ZAJobTemplate, run.job_template_id) if run.job_template_id else None
    return _out(run, tmpl)


@router.delete("/job-runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_run(
    run_id: str,
    role: str = Depends(get_user_role),
    session: AsyncSession = Depends(get_session),
):
    run = await session.get(ZAJobRun, run_id)
    if run is None:
        raise HTTPException(status_code=404)
    if run.status in ("success", "failed", "error", "cancelled"):
        raise HTTPException(status_code=400, detail="run already finished")
    run.status = "cancelled"
    run.ended_at = datetime.now(timezone.utc)
    await session.commit()
    redis = get_redis()
    await redis.publish(f"job:{run_id}:log", json.dumps({"type": "done", "status": "cancelled", "exit_code": None}))


@router.post("/job-runs/{run_id}/rerun", status_code=status.HTTP_202_ACCEPTED)
async def rerun(
    run_id: str,
    request: Request,
    user_id: str = Depends(get_user_id),
    session: AsyncSession = Depends(get_session),
):
    """Re-dispatch a finished run with the same template, params and targets."""
    orig = await session.get(ZAJobRun, run_id)
    if orig is None:
        raise HTTPException(status_code=404)
    tmpl = await session.get(ZAJobTemplate, orig.job_template_id)
    if tmpl is None:
        raise HTTPException(status_code=400, detail="job template no longer exists")
    if not tmpl.enabled:
        raise HTTPException(status_code=400, detail="template is disabled")

    params = dict(orig.params or {})
    targets = params.get("_target_host_ids") or list(tmpl.target_host_ids or [])

    new_id = str(uuid.uuid4())
    # _run_id must point at THIS new run, not the original — it's how a chain
    # template's ChainExecutor links each step's child run back to its parent
    # (see chain.py). Everything else in params (chain_steps, credential
    # selection, etc.) replays unchanged.
    if params.get("_run_id"):
        params["_run_id"] = new_id
    new_run = ZAJobRun(
        id=new_id, job_template_id=tmpl.id, triggered_by=f"user:{user_id}",
        status="pending", params=params, output_lines=[],
    )
    session.add(new_run)
    await session.commit()

    executors = getattr(request.app.state, "executors", {})
    executor = executors.get(tmpl.action_type)
    if executor is None:
        raise HTTPException(status_code=400, detail=f"no executor for action_type={tmpl.action_type}")

    from libs.pluginbase import RunRequest
    from app import runner as _runner

    req = RunRequest(
        action_type=tmpl.action_type,
        target_host_ids=targets,
        # Replay the original run's effective credential selection (_credential_id +
        # _host_credentials already live in params); fall back to the template credential.
        credential_id=params.get("_credential_id", tmpl.credential_id),
        # script_content/playbook_path come from the template only, never replayed
        # from stored params (defense-in-depth for the T11 injection path).
        params={**params, **template_code_params(tmpl)},
        triggered_by=f"user:{user_id}",
    )
    asyncio.create_task(_runner.execute(
        new_id, executor, req, timeout_seconds=tmpl.timeout_seconds, template_name=tmpl.name,
        retry_count=tmpl.retry_count, retry_delay_seconds=tmpl.retry_delay_seconds,
    ))
    return {"run_id": new_id}


@router.post("/job-runs/{run_id}/approve", status_code=status.HTTP_202_ACCEPTED)
async def approve_run(
    run_id: str,
    request: Request,
    role: str = Depends(get_user_real_role),
    session: AsyncSession = Depends(get_session),
):
    run = await session.get(ZAJobRun, run_id)
    if run is None:
        raise HTTPException(status_code=404)
    if run.status != "pending_approval":
        raise HTTPException(status_code=400, detail="run is not pending approval")
    tmpl = await session.get(ZAJobTemplate, run.job_template_id)
    if tmpl is None:
        raise HTTPException(status_code=400, detail="job template no longer exists")
    if not _can_approve(role, tmpl.approver_role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role to approve this run")

    run.status = "pending"
    await session.commit()

    executors = getattr(request.app.state, "executors", {})
    executor = executors.get(tmpl.action_type)
    if executor is None:
        raise HTTPException(status_code=400, detail=f"no executor for action_type={tmpl.action_type}")

    from libs.pluginbase import RunRequest
    from app import runner as _runner

    params = dict(run.params or {})
    target_host_ids = params.get("_target_host_ids") or list(tmpl.target_host_ids or [])
    req = RunRequest(
        action_type=tmpl.action_type,
        target_host_ids=target_host_ids,
        credential_id=params.get("_credential_id", tmpl.credential_id),
        params={**params, **template_code_params(tmpl)},
        triggered_by=run.triggered_by,
    )
    asyncio.create_task(_runner.execute(
        run_id, executor, req, timeout_seconds=tmpl.timeout_seconds, template_name=tmpl.name,
        retry_count=tmpl.retry_count, retry_delay_seconds=tmpl.retry_delay_seconds,
    ))
    return {"run_id": run_id, "status": "pending"}


class RejectPayload(BaseModel):
    reason: str = ""


@router.post("/job-runs/{run_id}/reject")
async def reject_run(
    run_id: str,
    payload: RejectPayload,
    role: str = Depends(get_user_real_role),
    session: AsyncSession = Depends(get_session),
):
    run = await session.get(ZAJobRun, run_id)
    if run is None:
        raise HTTPException(status_code=404)
    if run.status != "pending_approval":
        raise HTTPException(status_code=400, detail="run is not pending approval")
    tmpl = await session.get(ZAJobTemplate, run.job_template_id)
    if not _can_approve(role, tmpl.approver_role if tmpl else None):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role to reject this run")

    params = dict(run.params or {})
    if payload.reason:
        params["_rejection_reason"] = payload.reason
    run.params = params
    run.status = "rejected"
    run.ended_at = datetime.now(timezone.utc)
    await session.commit()
    return {"run_id": run_id, "status": "rejected"}


# NOTE: registered at app-level in main.py (NOT via this /api/v1-prefixed router) so the
# path is /ws/jobs/{run_id}/log — matching the gateway WS proxy. A prefixed path makes
# Starlette reject the unmatched WebSocket handshake with HTTP 403.
async def ws_job_log(websocket: WebSocket, run_id: str):
    """Replays the buffered output first (so fast/already-finished jobs still show their
    log), then tails new lines from Redis with a keepalive ping for quiet long jobs."""
    await websocket.accept()

    # 1. Replay everything captured so far.
    async with SessionLocal() as session:
        run = await session.get(ZAJobRun, run_id)
    if run is None:
        await websocket.send_text(json.dumps({"type": "done", "status": "error", "exit_code": None}))
        await websocket.close()
        return
    sent = 0
    for line in (run.output_lines or []):
        await websocket.send_text(json.dumps({"type": "line", "line": line}))
        sent += 1
    if run.status in _TERMINAL:
        await websocket.send_text(json.dumps({"type": "done", "status": run.status, "exit_code": run.exit_code}))
        await websocket.close()
        return

    # 2. Tail live output from Redis.
    async with aioredis.from_url(get_settings().redis_url, decode_responses=True) as sub_client:
        async with sub_client.pubsub() as pubsub:
            await pubsub.subscribe(f"job:{run_id}:log")
            # The run may have finished during replay — re-check and flush the delta.
            async with SessionLocal() as session:
                run = await session.get(ZAJobRun, run_id)
            if run and run.status in _TERMINAL:
                for line in (run.output_lines or [])[sent:]:
                    await websocket.send_text(json.dumps({"type": "line", "line": line}))
                await websocket.send_text(json.dumps({"type": "done", "status": run.status, "exit_code": run.exit_code}))
                await pubsub.unsubscribe(f"job:{run_id}:log")
                await websocket.close()
                return
            try:
                while True:
                    msg = await asyncio.wait_for(pubsub.get_message(ignore_subscribe_messages=True), timeout=15)
                    if msg and msg["type"] == "message":
                        await websocket.send_text(msg["data"])
                        if json.loads(msg["data"]).get("type") == "done":
                            break
                    else:
                        # keepalive — don't close on quiet periods
                        await websocket.send_text(json.dumps({"type": "ping"}))
            except (asyncio.TimeoutError, WebSocketDisconnect):
                pass
            except Exception:
                pass
            finally:
                await pubsub.unsubscribe(f"job:{run_id}:log")


def _out(r: ZAJobRun, tmpl: ZAJobTemplate | None = None) -> dict:
    params = r.params or {}
    # Target hosts: prefer what the run actually targeted (stored at dispatch), then the
    # template's configured hosts as a fallback for older runs.
    target_host_ids = params.get("_target_host_ids") or (list(tmpl.target_host_ids or []) if tmpl else [])
    # Per-host credential overrides (host_id → credential_id) when the run used them.
    host_credentials = params.get("_host_credentials") or {}
    # Triggering principal: "user:<id>" | "schedule:<id>" | "zabbix_event:<id>" | "manual_trigger:...".
    tb = r.triggered_by or ""
    trig_kind, _, trig_ref = tb.partition(":")
    return {
        "id": r.id, "job_template_id": r.job_template_id,
        "job_template_name": tmpl.name if tmpl else None,
        "action_type": tmpl.action_type if tmpl else params.get("action_type"),
        "triggered_by": r.triggered_by,
        "triggered_by_kind": trig_kind or None,
        "triggered_by_user_id": trig_ref if trig_kind == "user" else None,
        "status": r.status,
        "params": params, "output_lines": r.output_lines,
        "exit_code": r.exit_code,
        "target_host_ids": target_host_ids,
        "credential_id": params.get("_credential_id") or (tmpl.credential_id if tmpl else None),
        "subject_credential_id": params.get("subject_credential_id") or (tmpl.subject_credential_id if tmpl else None),
        "host_credentials": host_credentials,
        "approver_role": tmpl.approver_role if tmpl else None,
        "parent_run_id": r.parent_run_id,
        "host_results": getattr(r, "host_results", None) or {},
        "diff_summary": getattr(r, "diff_summary", None) or {},
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "ended_at": r.ended_at.isoformat() if r.ended_at else None,
    }
