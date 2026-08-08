"""Recording integrity digest (frames_sha256) — risk register R-4

Session recordings had no hash, checksum or signature, so one could be altered
or swapped with nothing to detect it, while the audit row referencing that
session stayed hash-chained. This adds a SHA-256 over the canonical frame JSON,
written once at ingest and re-checked by /internal/recordings/{id}/verify.

Nullable and not backfilled: existing recordings have no digest to compare
against, and inventing one now would certify content nobody has verified. The
verify endpoint reports "no digest recorded" for those rather than passing them.

Revision ID: 003
Revises: 002
Create Date: 2026-08-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "za_recordings" in set(inspector.get_table_names()):
        existing_cols = {c["name"] for c in inspector.get_columns("za_recordings")}
        if "frames_sha256" not in existing_cols:
            op.add_column(
                "za_recordings",
                sa.Column("frames_sha256", sa.String(64), nullable=True),
            )


def downgrade() -> None:
    op.drop_column("za_recordings", "frames_sha256")
