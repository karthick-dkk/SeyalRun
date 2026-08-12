"""Alert evaluator — matches job/system events against alert rules and dispatches notifications."""

from __future__ import annotations

import datetime
import re
from typing import Any

import structlog

from ..database import AsyncSessionLocal
from ..models.domain import AlertRule, Job

log = structlog.get_logger(__name__)


async def evaluate_job_event(job_id: str, exit_code: int) -> None:
    """Called after a job finishes. Evaluates all enabled job_failed / job_slow rules."""
    async with AsyncSessionLocal() as db:
        j = await db.get(Job, job_id)
        if not j:
            return

        from sqlalchemy import select

        result = await db.execute(
            select(AlertRule).where(
                AlertRule.enabled == True,  # noqa: E712
                AlertRule.event_type.in_(["job_failed", "job_slow"]),
            )
        )
        rules = result.scalars().all()

        for rule in rules:
            event_payload = _build_job_event_payload(j, exit_code)

            if rule.event_type == "job_failed" and exit_code != 0:
                if _matches_job_failed_conditions(rule.conditions or {}, j):
                    await _dispatch(rule, "job_failed", event_payload, db)

            elif rule.event_type == "job_slow" and j.duration_seconds:
                threshold = (rule.conditions or {}).get("duration_threshold_seconds", 300)
                if j.duration_seconds > threshold:
                    await _dispatch(rule, "job_slow", event_payload, db)

        await db.commit()


async def evaluate_zabbix_event(event_data: dict[str, Any]) -> None:
    """Called when the webhook-receiver forwards a Zabbix problem event."""
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select

        result = await db.execute(
            select(AlertRule).where(
                AlertRule.enabled == True,  # noqa: E712
                AlertRule.event_type == "zabbix_webhook",
            )
        )
        rules = result.scalars().all()

        for rule in rules:
            if _matches_zabbix_conditions(rule.conditions or {}, event_data):
                await _dispatch(rule, "zabbix_webhook", event_data, db)

        await db.commit()


async def evaluate_connectivity_event(target: str) -> None:
    """Called when a circuit breaker opens (connectivity lost)."""
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select

        result = await db.execute(
            select(AlertRule).where(
                AlertRule.enabled == True,  # noqa: E712
                AlertRule.event_type == "connectivity_lost",
            )
        )
        rules = result.scalars().all()

        payload = {
            "event_type": "connectivity_lost",
            "target": target,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }

        for rule in rules:
            target_pattern = (rule.conditions or {}).get("target")
            if not target_pattern or re.search(target_pattern, target):
                await _dispatch(rule, "connectivity_lost", payload, db)

        await db.commit()


# ── Condition matchers ────────────────────────────────────────────────────────


def _matches_job_failed_conditions(conditions: dict, j: Job) -> bool:
    pattern = conditions.get("playbook_pattern")
    if pattern:
        # Playbook name check happens synchronously from job fields
        pass

    user_pattern = conditions.get("triggered_by_pattern")
    if user_pattern and not re.search(user_pattern, j.triggered_by or ""):
        return False

    return True


def _matches_zabbix_conditions(conditions: dict, event: dict) -> bool:
    severity_min = conditions.get("severity_min", 1)
    severity = event.get("severity", 1)
    if isinstance(severity, str):
        severity_map = {"information": 1, "warning": 2, "average": 3, "high": 4, "disaster": 5}
        severity = severity_map.get(severity.lower(), 1)
    if severity < severity_min:
        return False

    group_pattern = conditions.get("host_group_pattern")
    if group_pattern:
        host_group = event.get("host_group", "")
        if not re.search(group_pattern, host_group):
            return False

    return True


# ── Dispatch ──────────────────────────────────────────────────────────────────


async def _dispatch(
    rule: AlertRule,
    event_type: str,
    event_payload: dict,
    db,
) -> None:
    try:
        from .notifier import deliver_alert

        await deliver_alert(rule=rule, event_type=event_type, event_payload=event_payload, db=db)
        log.info("alert_dispatched", rule=rule.name, event=event_type)
    except Exception as exc:
        log.error("alert_dispatch_error", rule=rule.name, event=event_type, error=str(exc))


def _build_job_event_payload(j: Job, exit_code: int) -> dict:
    last_lines = (j.output_lines or [])[-10:]
    return {
        "job_id": str(j.id),
        "playbook_id": str(j.playbook_id) if j.playbook_id else None,
        "triggered_by": j.triggered_by,
        "exit_code": exit_code,
        "duration_seconds": j.duration_seconds,
        "last_output_lines": last_lines,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
    }
