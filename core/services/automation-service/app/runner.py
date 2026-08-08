"""Job run lifecycle: create DB record, dispatch executor, stream output to Redis."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Callable

import httpx
import redis.asyncio as aioredis

from libs.servicetoken import mint

from .config import get_settings
from .database import SessionLocal
from .models import ZAJobRun, ZANotification

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None

_SEVERITY_BY_STATUS = {"success": "info", "failed": "critical", "error": "critical"}


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def create_notification(
    *, user_id: str | None, severity: str, title: str, source_type: str,
    source_id: str | None = None, message: str = "",
) -> ZANotification:
    """The one place a ZANotification gets created — in-app row + redis pub/sub
    fan-out + best-effort per-group email dispatch (see _dispatch_group_alert).
    Used both in-process (job-run completion below) and via POST /internal/
    notifications for other services (PCI DSS Phase B: inventory-service's
    rotation-due sweep, identity-service's security-event alerts)."""
    notif = ZANotification(
        user_id=user_id, severity=severity, title=title, message=message,
        source_type=source_type, source_id=source_id,
    )
    async with SessionLocal() as session:
        session.add(notif)
        await session.commit()
        await session.refresh(notif)
    channel = f"notifications:{user_id}" if user_id else "notifications:broadcast"
    payload = json.dumps({
        "id": notif.id, "severity": severity, "title": notif.title, "message": notif.message,
        "source_type": source_type, "source_id": source_id,
        "created_at": notif.created_at.isoformat() if notif.created_at else None,
    })
    await get_redis().publish(channel, payload)

    # Per-group email alerting (v1.2) — broadcast notifications (no specific
    # user_id) have no single user's groups to resolve against, so they're out
    # of scope here; best-effort, must never affect the in-app notification
    # path above, which has already succeeded independently of this.
    if user_id:
        await _dispatch_group_alert(user_id, severity, notif.title)
    return notif


async def _notify_completion(run_id: str, template_name: str, triggered_by: str | None, final_status: str) -> None:
    # Only "user:<id>"-triggered runs target a specific inbox; anything else
    # (schedule/zabbix/chain-step, or no notify-worthy status) broadcasts to everyone.
    user_id = None
    if triggered_by and triggered_by.startswith("user:"):
        user_id = triggered_by[len("user:"):]
    severity = _SEVERITY_BY_STATUS.get(final_status)
    if severity is None:
        return
    await create_notification(
        user_id=user_id, severity=severity, title=f"{template_name or 'Job'}: {final_status}",
        source_type="job_run", source_id=run_id,
    )


async def _dispatch_group_alert(user_id: str, severity: str, title: str) -> None:
    settings = get_settings()
    try:
        token = mint("automation-service", "identity-service", settings.service_jwt_secret)
        async with httpx.AsyncClient(base_url=settings.identity_service_url, timeout=10) as client:
            await client.post(
                "/api/v1/internal/notifications/dispatch-alert",
                headers={"X-Service-Token": token},
                json={"user_id": user_id, "severity": severity, "subject": f"SeyalRun: {title}", "body": title},
            )
    except Exception:  # noqa: BLE001
        logger.exception("group alert dispatch failed (non-fatal, in-app notification already succeeded)")


async def execute(
    run_id: str, executor, request, settings=None, timeout_seconds: int | None = None,
    template_name: str = "", retry_count: int = 0, retry_delay_seconds: int = 30,
) -> None:
    """Run a job: update DB status, stream output lines to Redis, finalize.

    timeout_seconds is the template's own (optional) override — it can only tighten
    the effective timeout, never exceed settings.job_exec_timeout_seconds, so a
    misconfigured template can't evade the platform-wide ceiling.

    retry_count re-attempts a failed/error/timed-out run up to retry_count additional
    times (0 = today's exact behavior, one attempt). A user Cancel is never retried —
    cancel_run() sets status directly and this function doesn't see it, so no special
    case is needed here; a genuine task cancellation (asyncio.CancelledError, distinct
    from our own asyncio.TimeoutError) propagates straight out of the loop below rather
    than being caught by the generic `except Exception`, since CancelledError has
    inherited from BaseException (not Exception) since Python 3.8.
    """
    if settings is None:
        settings = get_settings()

    effective_timeout = settings.job_exec_timeout_seconds
    if timeout_seconds:
        effective_timeout = min(timeout_seconds, settings.job_exec_timeout_seconds)

    redis = get_redis()
    channel = f"job:{run_id}:log"
    max_lines = settings.max_output_lines

    async def publish_line(line: str) -> None:
        msg = json.dumps({"type": "line", "line": line})
        await redis.publish(channel, msg)
        async with SessionLocal() as session:
            run = await session.get(ZAJobRun, run_id)
            if run is not None:
                lines = list(run.output_lines or [])
                lines.append(line)
                run.output_lines = lines[-max_lines:]
                await session.commit()

    async with SessionLocal() as session:
        run = await session.get(ZAJobRun, run_id)
        if run is None:
            return
        run.status = "running"
        await session.commit()

    total_attempts = max(1, retry_count + 1)
    exit_code = None
    final_status = "error"
    diff_summary: dict | None = None
    for attempt in range(1, total_attempts + 1):
        if attempt > 1:
            await publish_line(f"=== attempt {attempt}/{total_attempts} ===")
        exit_code = None
        final_status = "error"
        try:
            result = await asyncio.wait_for(
                executor.execute(request, publish_line),
                timeout=effective_timeout,
            )
            exit_code = result.exit_code if result.exit_code is not None else (0 if result.ok else 1)
            final_status = "success" if result.ok else "failed"
            if result.output:
                await publish_line(result.output)
            if result.details.get("diff_summary"):
                diff_summary = result.details["diff_summary"]
        except asyncio.TimeoutError:
            final_status = "error"
            await publish_line(f"[error] job timed out after {effective_timeout}s")
        except Exception as exc:
            final_status = "error"
            await publish_line(f"[error] {exc}")
            logger.exception("job run %s failed (attempt %s/%s)", run_id, attempt, total_attempts)

        if final_status == "success" or attempt == total_attempts:
            break
        await publish_line(
            f"[retry] attempt {attempt}/{total_attempts} failed ({final_status}), "
            f"retrying in {retry_delay_seconds}s..."
        )
        await asyncio.sleep(retry_delay_seconds)

    async with SessionLocal() as session:
        run = await session.get(ZAJobRun, run_id)
        if run is not None:
            run.status = final_status
            run.exit_code = exit_code
            run.ended_at = datetime.now(timezone.utc)
            if diff_summary:
                run.diff_summary = diff_summary
            await session.commit()

    done_msg = json.dumps({"type": "done", "status": final_status, "exit_code": exit_code})
    await redis.publish(channel, done_msg)

    await _notify_completion(run_id, template_name, request.triggered_by, final_status)
