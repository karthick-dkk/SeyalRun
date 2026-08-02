"""Fix: za_credential_history gains wrapped_dek. Without this, rotating an
envelope-encrypted credential (wrapped_dek set — see 009) archived the prior
secret_ciphertext but not the DEK that decrypts it, and the live row's
wrapped_dek was overwritten in the same transaction — permanently
undecryptable history rows. Nullable, no backfill: rows created before this
migration already lost their DEK and cannot be recovered; only new rotations
are protected.

Revision ID: 011
Revises: 010
Create Date: 2026-07-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_credential_history")}
    if "wrapped_dek" not in cols:
        op.add_column("za_credential_history", sa.Column("wrapped_dek", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("za_credential_history", "wrapped_dek")
