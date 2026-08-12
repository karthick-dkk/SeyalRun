"""Forced first-login password rotation: za_users.must_change_password

Revision ID: 010
Revises: 009
Create Date: 2026-07-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("za_users")}
    if "must_change_password" not in cols:
        op.add_column(
            "za_users",
            sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    op.drop_column("za_users", "must_change_password")
