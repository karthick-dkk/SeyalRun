"""Guard against a template running concurrently with itself.

Nothing prevented it. A cron that overruns its own interval, a double-clicked
Run button, or a Zabbix trigger that fires twice all produced two live runs
against the same hosts. For an ordinary report that is wasteful; for
rotate_secret it is two processes racing to change the same account, with the
vault recording one of the two outcomes and the host keeping the other — the
credential then no longer opens the host it belongs to.

Enforced by querying live runs rather than by an in-process lock: this service
can run more than one worker, and a lock in one of them says nothing about the
others. The database row IS the shared state.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ZAJobRun

# Statuses that mean "this run still owns the hosts".
LIVE_STATUSES = ("pending", "running", "pending_approval")


async def live_run_id(session: AsyncSession, template_id: str) -> str | None:
    """The id of a run of this template that is already live, if any."""
    result = await session.execute(
        select(ZAJobRun.id)
        .where(ZAJobRun.job_template_id == template_id)
        .where(ZAJobRun.status.in_(LIVE_STATUSES))
        .limit(1)
    )
    return result.scalar_one_or_none()
