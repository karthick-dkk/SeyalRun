"""Add credential_ids (multi-credential) to authorizations

Revision ID: 006
Revises: 005
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_authorizations")}
    if "credential_ids" not in cols:
        op.add_column("za_authorizations", sa.Column("credential_ids", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("za_authorizations", "credential_ids")
