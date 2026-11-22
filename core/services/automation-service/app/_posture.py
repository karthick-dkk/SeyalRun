"""The built-in "collect posture facts" job template.

posture-service never opens SSH itself: it asks this service for a facts run
and reads the run's output back. Each run still needs a ZAJobTemplate row to
hang off (history, approval, audit and the UI all key on job_template_id), so
one system template is kept here, found by its action_type rather than by
name so a rename in the UI does not orphan it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ZAJobTemplate, ZAProject

POSTURE_ACTION = "posture_facts"
POSTURE_PROJECT = "Posture"
POSTURE_TEMPLATE_NAME = "System · Collect Posture Facts"

# Template default_params an admin may set to change HOW the fixed script is
# run (e.g. under sudo where dnf metadata is root-only) — never what runs.
TUNABLE_PARAMS = ("use_sudo", "sudo_credential_id")


async def ensure_posture_template(session: AsyncSession) -> ZAJobTemplate:
    res = await session.execute(
        select(ZAJobTemplate).where(ZAJobTemplate.action_type == POSTURE_ACTION).limit(1)
    )
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
        project_id=proj.id, name=POSTURE_TEMPLATE_NAME, action_type=POSTURE_ACTION,
        description="Read-only: OS, kernel, installed and pending packages, reboot flag. "
                    "Triggered by posture-service; the script is fixed in the image.",
        target_scope="hosts", enabled=True, max_parallel=1,
    )
    session.add(tmpl)
    await session.commit()
    await session.refresh(tmpl)
    return tmpl


def tunable_params(tmpl: ZAJobTemplate) -> dict:
    return {k: v for k, v in (tmpl.default_params or {}).items() if k in TUNABLE_PARAMS}
