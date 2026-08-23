"""Cron schedule poller: fires job runs for enabled schedules whose next_run_at has passed."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from croniter import croniter

try:                                    # py3.9+ stdlib
    from zoneinfo import ZoneInfo
except ImportError:                     # pragma: no cover - not reachable on 3.12
    ZoneInfo = None                     # type: ignore[assignment]


def _local_now(sched, now):
    """`now` expressed in the schedule's own timezone.

    Cron was evaluated against UTC, so "0 2 * * *" meant 2am UTC — the wrong hour
    for almost everyone, and it silently shifted by an hour twice a year because
    UTC has no DST while the operator's expectation does. Evaluating in the
    schedule's zone makes 2am mean 2am there, across the transition.

    An unset or unknown zone falls back to UTC, which is exactly the previous
    behaviour — so existing schedules keep firing when they always did rather
    than jumping to a new hour on deploy.
    """
    name = getattr(sched, "timezone", None) or "UTC"
    if ZoneInfo is None:
        return now
    try:
        return now.astimezone(ZoneInfo(name))
    except Exception:
        return now
from sqlalchemy import and_, or_, select

from ._params import template_code_params
from ._production_gate import any_production_hosts
from .config import get_settings
from .database import SessionLocal
from ._concurrency import live_run_id
from .models import ZAJobRun, ZASchedule, ZAJobTemplate

logger = logging.getLogger(__name__)


# How long a run may sit in pending_approval, or run past its own ceiling, before
# anyone is told. Long enough not to nag; short enough that an approval requested
# before lunch is chased the same afternoon.
STALE_APPROVAL_SECONDS = 60 * 60
STUCK_RUN_GRACE_SECONDS = 60 * 5


async def _alert_stalled_runs(session, now) -> None:
    """Notify about runs that have gone quiet.

    Completion was the only notified event, so the two states that most need a
    human — an approval nobody has actioned, and a run that has outlived its own
    timeout without reporting — produced no signal at all. A job that never
    finishes never notifies, which is precisely backwards: the runs worth
    interrupting someone for are the ones not making progress.

    Notified ONCE per run, tracked by a marker in params, so a stalled run does
    not re-alert on every scheduler tick and train people to ignore it.
    """
    from datetime import timedelta

    from .models import ZAJobRun
    from .notifications import create_notification

    stale_approval = now - timedelta(seconds=STALE_APPROVAL_SECONDS)
    stuck_cutoff = now - timedelta(
        seconds=get_settings().job_exec_timeout_seconds + STUCK_RUN_GRACE_SECONDS
    )

    rows = (await session.execute(
        select(ZAJobRun).where(
            or_(
                and_(ZAJobRun.status == "pending_approval", ZAJobRun.started_at < stale_approval),
                and_(ZAJobRun.status == "running", ZAJobRun.started_at < stuck_cutoff),
            )
        ).limit(50)
    )).scalars().all()

    for run in rows:
        params = dict(run.params or {})
        if params.get("_stall_notified"):
            continue
        waiting = run.status == "pending_approval"
        await create_notification(
            user_id=None,          # nobody specific owns a stall; tell everyone who can act
            severity="medium" if waiting else "critical",
            title=(
                "Approval still pending" if waiting
                else "Job run has outlived its timeout without finishing"
            ),
            source_type="job_run", source_id=run.id,
        )
        params["_stall_notified"] = True
        run.params = params
    if rows:
        await session.commit()


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
        # Runs the scheduler is not otherwise looking at: an approval nobody has
        # actioned, or a run that has outlived its own ceiling. Guarded so a
        # failure here cannot stop schedules from firing — a missed alert is bad,
        # a stopped scheduler is worse.
        try:
            await _alert_stalled_runs(session, now)
        except Exception:
            logger.exception("stalled-run check failed")

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

            # The overrun case this guard exists for: a schedule firing again while
            # its previous run is still going. Skipped, not queued — and the cron
            # cursor still advances below, so the schedule stays on its intended
            # cadence instead of trying to catch up with a backlog.
            busy = await live_run_id(session, sched.job_template_id)
            if busy:
                logger.warning(
                    "scheduled run skipped — previous run still live",
                    extra={"schedule": sched.name, "template_id": sched.job_template_id,
                           "live_run_id": busy},
                )
                ci = croniter(sched.cron_expression, now)
                sched.next_run_at = ci.get_next(datetime)
                sched.last_run_at = now
                await session.commit()
                continue

            run = ZAJobRun(
                id=run_id,
                job_template_id=sched.job_template_id,
                triggered_by=f"schedule:{sched.id}",
                status="pending_approval" if force_approval else "pending",
                params=params,
                output_lines=[],
            )
            session.add(run)

            ci = croniter(sched.cron_expression, _local_now(sched, now))
            sched.next_run_at = ci.get_next(datetime).astimezone(timezone.utc)
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
