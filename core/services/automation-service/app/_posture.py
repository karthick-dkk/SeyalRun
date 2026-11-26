"""The built-in "collect posture facts" job template.

posture-service never opens SSH itself: it asks this service for a facts run
and reads the run's output back. Each run still needs a ZAJobTemplate row to
hang off (history, approval, audit and the UI all key on job_template_id), so
one system template is kept here, found by its action_type rather than by
name so a rename in the UI does not orphan it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ._concurrency import LIVE_STATUSES
from ._host_ops import APPROVED_BY_PARAM, HOST_OP_PARAM, OP_TOKEN_PARAM, READ_ONLY_OPS
from .models import ZAJobRun, ZAJobTemplate, ZAProject

POSTURE_ACTION = "posture_facts"
POSTURE_PROJECT = "Posture"
POSTURE_TEMPLATE_NAME = "System · Collect Posture Facts"

HOST_OP_TEMPLATES = {
    "package_ops": ("System · Package Operations",
                    "Package changes on one host. Started from Posture → Patching only; the script is fixed "
                    "in the image."),
    "host_reboot": ("System · Snapshot And Reboot",
                    "Host state snapshot, and reboot with a wait until the host is back. Started from "
                    "Posture → Patching only; the script is fixed in the image."),
}

# Template default_params an admin may set to change HOW the fixed script is
# run (e.g. under sudo where dnf metadata is root-only) — never what runs.
TUNABLE_PARAMS = ("use_sudo", "sudo_credential_id")


async def _ensure(session: AsyncSession, action: str, name: str, description: str) -> ZAJobTemplate:
    res = await session.execute(select(ZAJobTemplate).where(ZAJobTemplate.action_type == action)
                                .order_by(ZAJobTemplate.created_at).limit(1))
    tmpl = res.scalar_one_or_none()
    if tmpl is not None:
        return tmpl
    res = await session.execute(select(ZAProject).where(ZAProject.name == POSTURE_PROJECT).limit(1))
    proj = res.scalar_one_or_none()
    if proj is None:
        proj = ZAProject(name=POSTURE_PROJECT, description="Built-in posture management jobs",
                         source_type="local")
        session.add(proj)
        await session.flush()
    tmpl = ZAJobTemplate(
        project_id=proj.id, name=name, action_type=action, description=description,
        target_scope="hosts", enabled=True, max_parallel=1,
    )
    session.add(tmpl)
    await session.commit()
    await session.refresh(tmpl)
    return tmpl


async def host_op_runs(session: AsyncSession, host_id: str) -> list[ZAJobRun]:
    """Unfinished package/reboot runs on one host, read-only ones included."""
    ids = (await session.execute(select(ZAJobTemplate.id).where(
        ZAJobTemplate.action_type.in_(list(HOST_OP_TEMPLATES))))).scalars().all()
    res = await session.execute(select(ZAJobRun).where(
        ZAJobRun.job_template_id.in_(ids or [""]), ZAJobRun.status.in_(LIVE_STATUSES)))
    return [r for r in res.scalars().all() if host_id in ((r.params or {}).get("_target_host_ids") or [])]


async def mutating_host_ops(session: AsyncSession, host_id: str, *, exclude: str = "", token: str = "") -> list[ZAJobRun]:
    """Operations changing the host right now (a request still waiting is not one)."""
    from . import runner

    return [r for r in await host_op_runs(session, host_id)
            if r.id != exclude and r.id in runner.LIVE_RUNS
            and not (token and (r.params or {}).get(OP_TOKEN_PARAM) == token)
            and ((r.params or {}).get(HOST_OP_PARAM) or {}).get("op") not in READ_ONLY_OPS]


async def earlier_approver(session: AsyncSession, template_id: str, host_id: str, token: str, host_op: dict) -> str | None:
    """Who approved an earlier run of exactly this operation. A follower that died must
    not need a second approval for an operation the host may already be running."""
    res = await session.execute(select(ZAJobRun).where(
        ZAJobRun.job_template_id == template_id, ZAJobRun.params[OP_TOKEN_PARAM].as_string() == token))
    for run in res.scalars().all():
        p = run.params or {}
        if p.get(APPROVED_BY_PARAM) and p.get(HOST_OP_PARAM) == host_op and p.get("_target_host_ids") == [host_id]:
            return p[APPROVED_BY_PARAM]
    return None


async def reconcile_interrupted(session: AsyncSession) -> int:
    """At startup nothing is running in this process, so a fixed-script run that says
    it is has lost its task. "error" makes posture-service follow the operation again
    with the same token (package/reboot) or collect again (facts)."""
    ids = (await session.execute(select(ZAJobTemplate.id).where(
        ZAJobTemplate.action_type.in_([POSTURE_ACTION, *HOST_OP_TEMPLATES])))).scalars().all()
    rows = (await session.execute(select(ZAJobRun).where(
        ZAJobRun.job_template_id.in_(ids or [""]), ZAJobRun.status.in_(("pending", "running"))))).scalars().all()
    for run in rows:
        run.status, run.ended_at = "error", datetime.now(timezone.utc)
        run.output_lines = [*(run.output_lines or []), "[error] interrupted by a service restart"]
    await session.commit()
    return len(rows)


async def ensure_posture_template(session: AsyncSession) -> ZAJobTemplate:
    return await _ensure(
        session, POSTURE_ACTION, POSTURE_TEMPLATE_NAME,
        "Read-only: OS, kernel, installed and pending packages, reboot flag. "
        "Triggered by posture-service; the script is fixed in the image.")


async def ensure_host_op_template(session: AsyncSession, action: str) -> ZAJobTemplate:
    name, description = HOST_OP_TEMPLATES[action]
    return await _ensure(session, action, name, description)


def tunable_params(tmpl: ZAJobTemplate) -> dict:
    return {k: v for k, v in (tmpl.default_params or {}).items() if k in TUNABLE_PARAMS}
