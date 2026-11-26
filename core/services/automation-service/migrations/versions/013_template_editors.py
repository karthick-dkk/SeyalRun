"""automation-service: who has edited a job template (append-only).

A rollout's drain hook is a template. Its editors wrote code that runs as root, so
none of them may approve the rollout that runs it.

Revision ID: auto_013
Revises: auto_012
Create Date: 2026-11-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "auto_013"
down_revision = "auto_012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("za_job_templates")}
    if "editor_ids" not in cols:
        op.add_column("za_job_templates", sa.Column("editor_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("za_job_templates", "editor_ids")
