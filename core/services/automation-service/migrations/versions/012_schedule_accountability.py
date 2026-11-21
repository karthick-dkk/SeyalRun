"""Schedules record who created and last changed them.

A schedule targeting a production-tagged host holds its run for approval, and the
run's triggered_by is "schedule:<id>" — no person. Without knowing who set the
schedule up, that person could approve the run their own schedule produced, which
is the separation-of-duties gap job approval exists to close (F-2). The scheduler
copies these ids onto each run and approve_run refuses them.

Nullable: schedules created before this have no recorded author; re-saving one
records the editor.

Revision ID: auto_012
Revises: 011
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "auto_012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_schedules")}
    if "created_by" not in cols:
        op.add_column("za_schedules", sa.Column("created_by", sa.String(36), nullable=True))
    if "updated_by" not in cols:
        op.add_column("za_schedules", sa.Column("updated_by", sa.String(36), nullable=True))


def downgrade() -> None:
    op.drop_column("za_schedules", "updated_by")
    op.drop_column("za_schedules", "created_by")
