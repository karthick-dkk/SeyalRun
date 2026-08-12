"""T15: za_login_attempts table for DB-backed brute-force lockout

Revision ID: 007
Revises: 006
Create Date: 2026-06-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "za_login_attempts" not in set(inspector.get_table_names()):
        op.create_table(
            "za_login_attempts",
            sa.Column("key", sa.String(320), primary_key=True),
            sa.Column("fail_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table("za_login_attempts")
