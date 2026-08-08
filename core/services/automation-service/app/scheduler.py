"""Cron schedule poller: fires job runs for enabled schedules whose next_run_at has passed."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select

from ._params import template_code_params
from ._production_gate import any_production_hosts
from .config import get_settings
from .database import SessionLocal
from .models import ZAJobRun, ZASchedule, ZAJobTemplate

logger = logging.getLogger(__name__)


async def scheduler_loop(executors: dict) -> None:
    settings = get_settings()
    while True:
        try:
            await _tick(executors, settings)
        except Exception:
            logger.exception("scheduler tick error")
        await asyncio.sleep(60)


async def _tick(executors: dict, settings) -> None:
    from . import runner as _runner

    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        result = await session.execute(
            select(ZASchedule).where(
                ZASchedule.enabled.is_(True),
                ZASchedule.next_run_at <= now,
            )
        )
        schedules = result.scalars().all()

        for sched in schedules:
            tmpl = await session.get(ZAJobTemplate, sched.job_template_id)
            if tmpl is None or not tmpl.enabled:
                continue

            run_id = str(uuid.uuid4())
            params = {**tmpl.default_params, **sched.params_override, **template_code_params(tmpl)}
            target_host_ids = list(tmpl.target_host_ids or [])
            # PCI DSS Phase D — production is gated regardless of trigger mechanism;
            # a cron schedule targeting a production host is not exempt just because
            # nobody is watching it fire. See app/_production_gate.py.
            force_approval = await any_production_hosts(target_host_ids)
            run = ZAJobRun(
                id=run_id,
                job_template_id=sched.job_template_id,
                triggered_by=f"schedule:{sched.id}",
                status="pending_approval" if force_approval else "pending",
                params=params,
                output_lines=[],
            )
            session.add(run)

            ci = croniter(sched.cron_expression, now)
            sched.next_run_at = ci.get_next(datetime)
            sched.last_run_at = now
            await session.commit()

            if force_approval:
                logger.info("scheduled run held for approval (production host)", extra={"run_id": run_id, "schedule": sched.name})
                continue

            executor = executors.get(tmpl.action_type)
            if executor is None:
                logger.warning("no executor for action_type=%s (run_id=%s)", tmpl.action_type, run_id)
                continue

            from libs.pluginbase import RunRequest
            request = RunRequest(
                action_type=tmpl.action_type,
                target_host_ids=target_host_ids,
                credential_id=tmpl.credential_id,
                params=params,
                triggered_by=f"schedule:{sched.id}",
            )
            asyncio.create_task(_runner.execute(run_id, executor, request, settings))
            logger.info("scheduled job run started", extra={"run_id": run_id, "schedule": sched.name})
