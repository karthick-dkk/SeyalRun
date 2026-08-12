"""initial recording schema

Revision ID: 001
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = inspector.get_table_names()

    if "za_recordings" not in existing:
        op.create_table(
            "za_recordings",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("session_id", sa.String(36), nullable=False, unique=True),
            sa.Column("format", sa.String(20), nullable=False, server_default="frames_v1"),
            sa.Column("frames", sa.JSON, nullable=False, server_default="[]"),
            sa.Column("duration_seconds", sa.Numeric(10, 2), nullable=False, server_default="0"),
            sa.Column("size_bytes", sa.Integer, nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table("za_recordings")
