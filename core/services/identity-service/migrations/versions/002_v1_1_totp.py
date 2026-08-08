"""v1.1: TOTP fields on za_users for MFA-gated credential reveal

Revision ID: 002
Revises: 001
Create Date: 2026-06-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "za_users" in set(inspector.get_table_names()):
        existing_cols = {c["name"] for c in inspector.get_columns("za_users")}
        if "totp_secret" not in existing_cols:
            op.add_column("za_users", sa.Column(
                "totp_secret", sa.Text(), nullable=False, server_default=""
            ))
        if "totp_enabled" not in existing_cols:
            op.add_column("za_users", sa.Column(
                "totp_enabled", sa.Boolean(), nullable=False, server_default="false"
            ))


def downgrade() -> None:
    op.drop_column("za_users", "totp_enabled")
    op.drop_column("za_users", "totp_secret")
