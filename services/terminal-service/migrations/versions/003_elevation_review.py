"""PCI DSS Phase A: za_ssh_sessions gains elevation_used/reviewed_at/reviewed_by —
tracks sessions created via the admin/superadmin JIT elevation fallback (no
ZAAuthorization grant existed for the host) and lets an admin sign off on them
after the fact, closing the checklist's "break-glass access... mandatory
post-use review" gap.

Revision ID: term_003
Revises: term_002
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "term_003"
down_revision = "term_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_ssh_sessions")}
    if "elevation_used" not in cols:
        op.add_column(
            "za_ssh_sessions",
            sa.Column("elevation_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "reviewed_at" not in cols:
        op.add_column("za_ssh_sessions", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    if "reviewed_by" not in cols:
        op.add_column("za_ssh_sessions", sa.Column("reviewed_by", sa.String(36), nullable=True))


def downgrade() -> None:
    op.drop_column("za_ssh_sessions", "reviewed_by")
    op.drop_column("za_ssh_sessions", "reviewed_at")
    op.drop_column("za_ssh_sessions", "elevation_used")
