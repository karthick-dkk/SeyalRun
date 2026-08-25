"""Schedules carry the timezone their cron is evaluated in.

Cron was evaluated against UTC, so "0 2 * * *" meant 2am UTC — the wrong hour for
almost every operator, and it shifted by an hour twice a year because UTC has no
DST while the expectation behind "run at 2am" does.

Defaults to UTC so existing schedules keep firing exactly when they always have.
A deploy that silently moved everyone's nightly job to a different hour would be
a worse bug than the one being fixed.

Revision ID: 011
Revises: auto_010
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "011"
down_revision = "auto_010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_schedules")}
    if "timezone" not in cols:
        op.add_column(
            "za_schedules",
            sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        )


def downgrade() -> None:
    op.drop_column("za_schedules", "timezone")
