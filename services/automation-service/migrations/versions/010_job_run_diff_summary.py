"""PCI DSS Phase D: za_job_runs gains diff_summary — structured, per-host
change-control data parsed from ansible_playbook's --check --diff output at
publish time. Nullable; only populated for dry-run ansible jobs.

Revision ID: auto_010
Revises: auto_009
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "auto_010"
down_revision = "auto_009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_job_runs")}
    if "diff_summary" not in cols:
        op.add_column("za_job_runs", sa.Column("diff_summary", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("za_job_runs", "diff_summary")
