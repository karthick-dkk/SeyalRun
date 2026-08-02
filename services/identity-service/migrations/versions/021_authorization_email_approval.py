"""Email-based approval: za_authz_approval_tokens — single-use, per-recipient
magic-link tokens letting an admin/superadmin approve or reject a pending
ZAAuthorization straight from an email, without logging in first. Only a
sha256 hash of each token is stored.

Revision ID: 021
Revises: 020
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())

    if "za_authz_approval_tokens" not in existing_tables:
        op.create_table(
            "za_authz_approval_tokens",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("authorization_id", sa.String(36), sa.ForeignKey("za_authorizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("approver_user_id", sa.String(36), sa.ForeignKey("za_users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("action", sa.String(10), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_za_authz_approval_tokens_token_hash", "za_authz_approval_tokens", ["token_hash"])
        op.create_index("ix_za_authz_approval_tokens_authorization_id", "za_authz_approval_tokens", ["authorization_id"])


def downgrade() -> None:
    op.drop_table("za_authz_approval_tokens")
