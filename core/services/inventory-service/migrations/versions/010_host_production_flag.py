"""PCI DSS Phase D: za_hosts gains is_production — automation-service forces
any job run targeting a production host through the approval flow
(ZAJobTemplate.requires_approval path), regardless of the template's own
setting. Defaults false so existing hosts are unaffected until an admin
explicitly tags a host as production.

Revision ID: 010
Revises: 009
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_hosts")}
    if "is_production" not in cols:
        op.add_column(
            "za_hosts",
            sa.Column("is_production", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    op.drop_column("za_hosts", "is_production")
