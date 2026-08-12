"""created_by column on za_job_templates (template author tracking)

Adds the nullable creator column shown in the Automation UI's "Created by" field.
Existing rows are left NULL — there's no reliable historical creator to backfill.

Revision ID: auto_005
Revises: auto_004
Create Date: 2026-07-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "auto_005"
down_revision = "auto_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "za_job_templates" in set(inspector.get_table_names()):
        existing_cols = {c["name"] for c in inspector.get_columns("za_job_templates")}
        if "created_by" not in existing_cols:
            op.add_column(
                "za_job_templates",
                sa.Column("created_by", sa.String(length=36), nullable=True),
            )


def downgrade() -> None:
    op.drop_column("za_job_templates", "created_by")
