"""Add sudo + push-account flags to credentials

Revision ID: 003
Revises: 002
Create Date: 2026-06-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("za_credentials")}
    if "is_sudo" not in cols:
        op.add_column("za_credentials", sa.Column("is_sudo", sa.Boolean(), nullable=False, server_default="false"))
    if "is_push_account" not in cols:
        op.add_column("za_credentials", sa.Column("is_push_account", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("za_credentials", "is_push_account")
    op.drop_column("za_credentials", "is_sudo")
