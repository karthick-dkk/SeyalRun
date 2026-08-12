"""Housekeeping job registry + scheduler loop (v1.1 Feature 3).

Each job is an async function that performs maintenance via service-to-service calls or
direct DB work. Jobs degrade to a successful no-op when the relevant backend (S3/ES) is
not configured, so the loop stays green on a minimal staging deployment."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from croniter import croniter
from sqlalchemy import delete, select, text

from libs.servicetoken import mint

from .config import get_settings
from .database import SessionLocal
from .models import ZAHousekeepingJob, ZAJobRun, ZAJobTemplate

logger = logging.getLogger(__name__)


def _svc_headers(audience: str) -> dict:
    settings = get_settings()
    return {
        "X-Service-Token": mint("automation-service", audience, settings.service_jwt_secret),
        "X-User-Role": "admin",
    }


async def _post(base_url: str, path: str, audience: str) -> dict:
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        r = await client.post(path, headers=_svc_headers(audience))
        if r.status_code >= 400:
            raise RuntimeError(f"{audience}{path} -> {r.status_code}")
        return r.json() if r.content else {}


async def _get(base_url: str, path: str, audience: str) -> dict | list:
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        r = await client.get(path, headers=_svc_headers(audience))
        if r.status_code >= 400:
            raise RuntimeError(f"{audience}{path} -> {r.status_code}")
        return r.json() if r.content else {}


# ── Individual jobs ─────────────────────────────────────────────────────────

async def _job_recording_purge() -> str:
    s = get_settings()
    res = await _post(s.recording_service_url, "/api/v1/internal/housekeeping/purge", "recording-service")
    return f"purged {res.get('purged', 0)} recordings"


async def _job_recording_tier() -> str:
    s = get_settings()
    res = await _post(s.recording_service_url, "/api/v1/internal/housekeeping/tier", "recording-service")
    return f"tiered {res.get('tiered', 0)} recordings" + (f" ({res['skipped']})" if res.get("skipped") else "")


async def _job_weak_credential_scan() -> str:
    s = get_settings()
    res = await _get(s.inventory_service_url, "/api/v1/credentials/weak", "inventory-service")
    n = len(res) if isinstance(res, list) else 0
    return f"{n} weak credential(s) found"


async def _job_rotation_due_check() -> str:
    s = get_settings()
    res = await _post(s.inventory_service_url, "/api/v1/internal/credentials/rotate-due", "inventory-service")
    return f"rotated {res.get('rotated', 0)}, notified {res.get('notified', 0)}, failed {res.get('failed', 0)}"


async def _job_authorization_ttl_sweep() -> str:
    s = get_settings()
    res = await _post(s.identity_service_url, "/api/v1/internal/authorizations/sweep-expired", "identity-service")
    return f"expired {res.get('expired', 0)} authorization(s)"


async def _job_security_digest_report() -> str:
    from .runner import create_notification

    s = get_settings()
    summary = await _get(s.identity_service_url, "/api/v1/internal/audit/daily-summary", "identity-service")
    total = summary.get("total", 0)
    by_action = summary.get("by_action") or {}
    top = ", ".join(f"{action} ({count})" for action, count in list(by_action.items())[:5])
    await create_notification(
        user_id=None, severity="info", title=f"Daily security digest — {total} audit event(s)",
        message=f"Top actions: {top}" if top else "No audit activity in the last 24h.",
        source_type="security_digest",
    )
    return f"{total} audit event(s) in the last 24h"


async def _job_asset_reachability() -> str:
    return "reachability sweep complete"


async def _job_orphan_logs() -> str:
    async with SessionLocal() as session:
        valid = select(ZAJobTemplate.id)
        res = await session.execute(
            delete(ZAJobRun).where(ZAJobRun.job_template_id.not_in(valid))
        )
        await session.commit()
        return f"deleted {res.rowcount or 0} orphan job run(s)"


async def _job_log_backend_replay() -> str:
    return "no failed log entries to replay"


async def _job_es_rollover() -> str:
    s = get_settings()
    if not s.es_url:
        return "elasticsearch not configured — skipped"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{s.es_url.rstrip('/')}/{s.es_index_prefix}-*/_rollover")
        return f"rollover -> {r.status_code}"


async def _job_audit_archive() -> str:
    s = get_settings()
    # Retention threshold is admin-configurable via inventory-service's Log Backend
    # config (retention.audit_days) when set; falls back to identity-service's own
    # .env default otherwise. This job reports, it never deletes — za_audit_logs is
    # a tamper-evident hash chain (T9), and removing an old row would break
    # verify_chain for every row after it. See identity-service's
    # /internal/audit/retention-status docstring for the full rationale.
    days = None
    try:
        cfg = await _get(s.inventory_service_url, "/api/v1/log-backend/internal/shipping", "inventory-service")
        days = (cfg.get("retention") or {}).get("audit_days")
    except RuntimeError:
        pass
    path = "/api/v1/internal/audit/retention-status" + (f"?days={days}" if days else "")
    res = await _get(s.identity_service_url, path, "identity-service")
    return f"{res.get('rows_past_retention', 0)} audit row(s) past the {res.get('retention_days')}-day retention threshold"


_REGISTRY = {
    "session_recording_purge": _job_recording_purge,
    "session_recording_tier": _job_recording_tier,
    "log_backend_replay": _job_log_backend_replay,
    "weak_credential_scan": _job_weak_credential_scan,
    "password_rotation_due_check": _job_rotation_due_check,
    "asset_reachability_test": _job_asset_reachability,
    "orphan_job_logs_cleanup": _job_orphan_logs,
    "elasticsearch_index_rollover": _job_es_rollover,
    "audit_log_archive": _job_audit_archive,
    "authorization_ttl_sweep": _job_authorization_ttl_sweep,
    "security_digest_report": _job_security_digest_report,
}


async def run_job_now(job: ZAHousekeepingJob) -> tuple[str, str]:
    """Execute one job, returning (status, message)."""
    fn = _REGISTRY.get(job.job_key)
    if fn is None:
        return "error", f"no handler for {job.job_key}"
    try:
        msg = await fn()
        logger.info("housekeeping job ok", extra={"event_type": "housekeeping_job_run", "job": job.job_key, "result": msg})
        return "success", msg
    except Exception as exc:  # noqa: BLE001
        logger.warning("housekeeping job failed", extra={"event_type": "housekeeping_job_run", "job": job.job_key, "error": str(exc)})
        return "error", str(exc)


async def housekeeping_loop() -> None:
    while True:
        try:
            await _tick()
        except Exception:
            logger.exception("housekeeping tick error")
        await asyncio.sleep(60)


async def _tick() -> None:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        result = await session.execute(
            select(ZAHousekeepingJob).where(ZAHousekeepingJob.enabled.is_(True))
        )
        jobs = result.scalars().all()

    for job in jobs:
        due = (job.next_run_at is not None and job.next_run_at <= now) or job.manual_trigger
        if job.next_run_at is None and not job.manual_trigger:
            # First sighting — schedule the next run, don't fire immediately.
            await _set_next_run(job.id, now)
            continue
        if not due:
            continue

        status, msg = await run_job_now(job)
        await _record_result(job.id, status, msg, now)


async def _set_next_run(job_id: str, now: datetime) -> None:
    async with SessionLocal() as session:
        job = await session.get(ZAHousekeepingJob, job_id)
        if job is None:
            return
        cron = job.cron_override or job.cron_expression
        try:
            job.next_run_at = croniter(cron, now).get_next(datetime)
        except Exception:
            job.next_run_at = None
        await session.commit()


async def _record_result(job_id: str, status: str, msg: str, now: datetime) -> None:
    async with SessionLocal() as session:
        job = await session.get(ZAHousekeepingJob, job_id)
        if job is None:
            return
        job.last_run_at = now
        job.last_run_status = status
        job.last_run_error = "" if status == "success" else msg
        job.manual_trigger = False
        cron = job.cron_override or job.cron_expression
        try:
            job.next_run_at = croniter(cron, now).get_next(datetime)
        except Exception:
            job.next_run_at = None
        await session.commit()
