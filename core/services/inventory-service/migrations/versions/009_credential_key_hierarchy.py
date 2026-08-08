"""PCI DSS Phase C: za_credentials gains wrapped_dek — the per-row Data
Encryption Key (wrapped by the active KeyProvider plugin) that encrypts
secret_ciphertext, replacing "one key encrypts every row directly" with a
real key hierarchy. Nullable, no backfill: existing rows keep decrypting
under the old single-KEK path (see app/vault.py::decrypt vs
decrypt_envelope) until their next write, at which point they're re-
encrypted under the envelope scheme automatically.

Revision ID: 009
Revises: 008
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_credentials")}
    if "wrapped_dek" not in cols:
        op.add_column("za_credentials", sa.Column("wrapped_dek", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("za_credentials", "wrapped_dek")
