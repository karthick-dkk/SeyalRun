"""T9: tamper-evident audit hash chain columns on za_audit_logs

Adds seq / prev_hash / entry_hash. Nullable (no backfill needed); every new row
written via log_action populates them under an advisory lock.

Revision ID: 008
Revises: 007
Create Date: 2026-06-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "za_audit_logs" in set(inspector.get_table_names()):
        cols = {c["name"] for c in inspector.get_columns("za_audit_logs")}
        if "seq" not in cols:
            op.add_column("za_audit_logs", sa.Column("seq", sa.BigInteger(), nullable=True))
            op.create_index("ix_za_audit_logs_seq", "za_audit_logs", ["seq"])
        if "prev_hash" not in cols:
            op.add_column("za_audit_logs", sa.Column("prev_hash", sa.String(64), nullable=True))
        if "entry_hash" not in cols:
            op.add_column("za_audit_logs", sa.Column("entry_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_index("ix_za_audit_logs_seq", table_name="za_audit_logs")
    op.drop_column("za_audit_logs", "entry_hash")
    op.drop_column("za_audit_logs", "prev_hash")
    op.drop_column("za_audit_logs", "seq")
