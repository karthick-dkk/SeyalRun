"""PCI DSS Phase A: za_audit_logs gains session_id + result columns, closing
the structured-event-schema gap (PCI 10.2) — user_id/event_type/timestamp/
source_ip/target_resource already existed, session_id/result did not.

Nullable, no backfill — the hash chain's per-row hash is fixed at write time,
so older rows simply carry these as None and still verify (see app/audit.py).

Revision ID: 017
Revises: 016
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_audit_logs")}
    if "session_id" not in cols:
        op.add_column("za_audit_logs", sa.Column("session_id", sa.String(36), nullable=True))
    if "result" not in cols:
        op.add_column("za_audit_logs", sa.Column("result", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("za_audit_logs", "result")
    op.drop_column("za_audit_logs", "session_id")
