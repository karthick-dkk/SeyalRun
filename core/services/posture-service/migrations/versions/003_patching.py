"""posture-service M3/M4: patch repos, package operations, maintenance windows,
baselines, rollouts with per-host state, rollout history.

Revision ID: posture_003
Revises: posture_002
Create Date: 2026-11-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "posture_003"
down_revision = "posture_002"
branch_labels = None
depends_on = None

_BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
_TS = sa.DateTime(timezone=True)


def _id() -> sa.Column:
    return sa.Column("id", sa.String(36), primary_key=True)


def _s(name: str, n: int, null: bool = False) -> sa.Column:
    return sa.Column(name, sa.String(n), nullable=null)


def _j(name: str) -> sa.Column:
    return sa.Column(name, sa.JSON(), nullable=False)


def _t(name: str) -> sa.Column:
    return sa.Column(name, sa.Text(), nullable=False)


def _ts(name: str) -> sa.Column:
    return sa.Column(name, _TS, nullable=True)


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    def create(name: str, *cols, indexes: tuple = ()) -> None:
        if name in existing:
            return
        op.create_table(name, *cols)
        for iname, icols in indexes:
            op.create_index(iname, name, list(icols))

    create("posture_patch_repos", _id(), sa.Column("slug", sa.String(40), nullable=False, unique=True),
           _s("family", 8), _s("base_url", 500), _s("suite", 80), _s("components", 200), _t("gpg_key"),
           _j("group_ids"), sa.Column("enabled", sa.Boolean(), nullable=False), _j("editor_ids"),
           _ts("created_at"), _ts("updated_at"))
    create("posture_package_ops", _id(), _s("host_id", 36), _s("op", 20), _j("args"), _s("status", 24),
           _s("job_run_id", 36, True), _j("result"), sa.Column("error", sa.Text(), nullable=True),
           _s("requested_by", 36), _s("requested_by_name", 200), _ts("created_at"), _ts("finished_at"),
           indexes=(("ix_posture_pkgops_host", ("host_id", "created_at")), ("ix_posture_pkgops_status", ("status",))))
    create("posture_patch_windows", _id(), sa.Column("name", sa.String(200), nullable=False, unique=True),
           _s("timezone", 64), _j("days"), _s("start", 5), sa.Column("duration_minutes", sa.Integer(), nullable=False),
           _j("editor_ids"), _ts("created_at"), _ts("updated_at"))
    create("posture_patch_baselines", _id(), sa.Column("name", sa.String(200), nullable=False, unique=True),
           _t("description"), _j("group_ids"), _j("config"), _j("editor_ids"), _ts("created_at"), _ts("updated_at"))
    create("posture_rollouts", _id(), _s("name", 200), _s("baseline_id", 36, True), _s("status", 24),
           _t("status_reason"), _j("plan"), _s("plan_hash", 64),
           sa.Column("current_wave", sa.Integer(), nullable=False), _ts("soak_until"), _s("waiting", 300),
           _s("created_by", 36), _s("created_by_name", 200), _j("accountable_ids"), _s("approved_by", 36, True),
           _s("approved_by_name", 200), _ts("approved_at"), _s("feed_version", 64), _ts("created_at"),
           _ts("started_at"), _ts("finished_at"),
           indexes=(("ix_posture_rollouts_status", ("status",)),))
    create("posture_rollout_hosts", _id(), _s("rollout_id", 36), _s("host_id", 36), _s("active_host_id", 36, True),
           _s("hostname", 255), sa.Column("wave", sa.Integer(), nullable=False), _s("status", 16), _s("step", 24),
           _ts("step_started_at"), sa.Column("attempt", sa.Integer(), nullable=False), _t("error"),
           sa.Column("drained", sa.Boolean(), nullable=False), _s("maintenance_id", 64, True),
           sa.Column("changed", sa.Boolean(), nullable=False), _j("runs"), _j("data"), _j("checks"),
           _ts("started_at"), _ts("finished_at"),
           sa.UniqueConstraint("rollout_id", "host_id", name="uq_posture_rollout_host"),
           sa.UniqueConstraint("active_host_id", name="uq_posture_rollout_active_host"),
           indexes=(("ix_posture_rollout_hosts_status", ("status",)),))
    create("posture_rollout_events", sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
           _s("rollout_id", 36), _s("host_id", 36, True), _ts("at"), _s("event", 64), _s("by", 200), _t("detail"),
           indexes=(("ix_posture_rollout_events", ("rollout_id", "id")),))


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for t in ("posture_rollout_events", "posture_rollout_hosts", "posture_rollouts", "posture_patch_baselines",
              "posture_patch_windows", "posture_package_ops", "posture_patch_repos"):
        if t in existing:
            op.drop_table(t)
