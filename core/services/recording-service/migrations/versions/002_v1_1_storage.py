"""v1.1: recording storage lifecycle fields (storage_location, storage_key, tiered_at)

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

    if "za_recordings" in set(inspector.get_table_names()):
        existing_cols = {c["name"] for c in inspector.get_columns("za_recordings")}
        if "storage_location" not in existing_cols:
            op.add_column("za_recordings", sa.Column(
                "storage_location", sa.String(20), nullable=False, server_default="local"
            ))
        if "storage_key" not in existing_cols:
            op.add_column("za_recordings", sa.Column(
                "storage_key", sa.Text(), nullable=False, server_default=""
            ))
        if "tiered_at" not in existing_cols:
            op.add_column("za_recordings", sa.Column(
                "tiered_at", sa.DateTime(timezone=True), nullable=True
            ))


def downgrade() -> None:
    op.drop_column("za_recordings", "tiered_at")
    op.drop_column("za_recordings", "storage_key")
    op.drop_column("za_recordings", "storage_location")
