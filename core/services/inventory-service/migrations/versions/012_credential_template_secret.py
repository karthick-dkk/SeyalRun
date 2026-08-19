"""Account Templates hold secret material, matching JumpServer PAM.

A template was metadata only — name, username, secret_type, rotation policy —
so "save a password on the template" silently kept nothing, which is the bug
reported. In JumpServer an Account Template carries a secret and creating an
Account from it *copies* that secret into the new per-asset account, which
re-encrypts it under its own DEK. The template is seed material, not a live
shared credential: one asset's compromise does not hand over every asset that
was seeded from the same template.

Both columns nullable — existing templates have no secret and must keep working
as pure defaults profiles.

Encrypted the same way ZACredential is (app/vault.encrypt_envelope: per-row DEK,
KEK-wrapped), deliberately: a second encryption scheme is a second thing for
ops/rotate_vault_key.py to miss, which is exactly how R-6 happened. That script
gains this table in the same commit as this migration — a wrapped secret it does
not rewrap becomes undecryptable at the next KEK rotation.

Revision ID: 012
Revises: 011
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_credential_templates")}
    if "secret_ciphertext" not in cols:
        op.add_column("za_credential_templates", sa.Column("secret_ciphertext", sa.Text(), nullable=True))
    if "wrapped_dek" not in cols:
        op.add_column("za_credential_templates", sa.Column("wrapped_dek", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("za_credential_templates", "wrapped_dek")
    op.drop_column("za_credential_templates", "secret_ciphertext")
