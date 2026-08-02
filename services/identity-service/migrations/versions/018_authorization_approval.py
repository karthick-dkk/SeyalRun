"""PCI DSS Phase B: za_authorizations gains a segregation-of-duties approval
workflow (status, requested_by, approved_by, approved_at). Existing rows are
backfilled by their current enabled value (enabled=true -> active, false ->
rejected) so this migration never revokes or newly-requires-approval for
access that was already granted — only rows created/edited after this ships
go through the pending_approval -> approve/reject flow.

Revision ID: 018
Revises: 017
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_authorizations")}
    if "status" not in cols:
        op.add_column(
            "za_authorizations",
            sa.Column("status", sa.String(20), nullable=False, server_default="pending_approval"),
        )
        op.execute(
            "UPDATE za_authorizations SET status = CASE WHEN enabled THEN 'active' ELSE 'rejected' END"
        )
    if "requested_by" not in cols:
        op.add_column("za_authorizations", sa.Column("requested_by", sa.String(36), nullable=True))
    if "approved_by" not in cols:
        op.add_column("za_authorizations", sa.Column("approved_by", sa.String(36), nullable=True))
    if "approved_at" not in cols:
        op.add_column("za_authorizations", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("za_authorizations", "approved_at")
    op.drop_column("za_authorizations", "approved_by")
    op.drop_column("za_authorizations", "requested_by")
    op.drop_column("za_authorizations", "status")
