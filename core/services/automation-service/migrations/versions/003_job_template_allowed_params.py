"""T5: allowed_param_keys column on za_job_templates (param allowlist)

Adds the per-template caller-param allowlist that, together with app/_params.py,
prevents webhook/UI-supplied params from injecting template-controlled keys
(script_content, playbook_path) into bash/ansible executors.

Revision ID: 003
Revises: 002
Create Date: 2026-06-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "za_job_templates" in set(inspector.get_table_names()):
        existing_cols = {c["name"] for c in inspector.get_columns("za_job_templates")}
        if "allowed_param_keys" not in existing_cols:
            op.add_column(
                "za_job_templates",
                sa.Column("allowed_param_keys", sa.JSON(), nullable=False, server_default="[]"),
            )


def downgrade() -> None:
    op.drop_column("za_job_templates", "allowed_param_keys")
