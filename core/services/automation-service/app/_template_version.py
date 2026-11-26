"""Which version of a template a run waiting for approval was requested with.

The script is read from the template when the run starts, so a template edited while the
run waited is not what was requested, and its new editor is not barred from approving.
Stamped on insert, so that no way of creating a run can leave it out.
"""

from __future__ import annotations

from sqlalchemy import event, select

from app.models import ZAJobRun, ZAJobTemplate

TEMPLATE_VERSION_PARAM = "_template_version"


def template_version(conn, template_id: str) -> str:
    """The template's last edit, and for a chain also that of every step."""
    t = ZAJobTemplate.__table__.c
    row = conn.execute(select(t.updated_at, t.action_type, t.chain_steps).where(t.id == template_id)).first()
    if row is None:
        return ""
    ids = [template_id]
    if row.action_type == "chain":
        ids += [s.get("template_id") for s in row.chain_steps or [] if s.get("template_id")]
    edited = dict(conn.execute(select(t.id, t.updated_at).where(t.id.in_(ids))).all())
    return ";".join(f"{i}={edited[i].isoformat() if edited.get(i) else ''}" for i in dict.fromkeys(ids))


@event.listens_for(ZAJobRun, "before_insert")
def _stamp(_mapper, conn, run) -> None:
    if run.status == "pending_approval":
        run.params = {**(run.params or {}), TEMPLATE_VERSION_PARAM: template_version(conn, run.job_template_id)}
