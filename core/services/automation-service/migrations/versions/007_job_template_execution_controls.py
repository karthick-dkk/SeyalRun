"""Execution-control columns on za_job_templates + parent_run_id on za_job_runs

Adds the columns backing timeout/retry/parallel-forks/approval/chaining
features on job templates, plus a nullable parent_run_id on job runs (set
only for a step dispatched by a "chain" template's own executor). All
additive and nullable-or-defaulted, so existing rows need no backfill.

Revision ID: auto_007
Revises: auto_006
Create Date: 2026-07-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "auto_007"
down_revision = "auto_006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "za_job_templates" in table_names:
        existing = {c["name"] for c in inspector.get_columns("za_job_templates")}
        if "timeout_seconds" not in existing:
            op.add_column("za_job_templates", sa.Column("timeout_seconds", sa.Integer(), nullable=True))
        if "retry_count" not in existing:
            op.add_column("za_job_templates", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
        if "retry_delay_seconds" not in existing:
            op.add_column("za_job_templates", sa.Column("retry_delay_seconds", sa.Integer(), nullable=False, server_default="30"))
        if "max_parallel" not in existing:
            op.add_column("za_job_templates", sa.Column("max_parallel", sa.Integer(), nullable=False, server_default="1"))
        if "forks" not in existing:
            op.add_column("za_job_templates", sa.Column("forks", sa.Integer(), nullable=True))
        if "requires_approval" not in existing:
            op.add_column("za_job_templates", sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default=sa.false()))
        if "approver_role" not in existing:
            op.add_column("za_job_templates", sa.Column("approver_role", sa.String(length=20), nullable=True))
        if "chain_steps" not in existing:
            op.add_column("za_job_templates", sa.Column("chain_steps", sa.JSON(), nullable=False, server_default="[]"))

    if "za_job_runs" in table_names:
        existing = {c["name"] for c in inspector.get_columns("za_job_runs")}
        if "parent_run_id" not in existing:
            op.add_column("za_job_runs", sa.Column("parent_run_id", sa.String(length=36), nullable=True))


def downgrade() -> None:
    op.drop_column("za_job_runs", "parent_run_id")
    op.drop_column("za_job_templates", "chain_steps")
    op.drop_column("za_job_templates", "approver_role")
    op.drop_column("za_job_templates", "requires_approval")
    op.drop_column("za_job_templates", "forks")
    op.drop_column("za_job_templates", "max_parallel")
    op.drop_column("za_job_templates", "retry_delay_seconds")
    op.drop_column("za_job_templates", "retry_count")
    op.drop_column("za_job_templates", "timeout_seconds")
