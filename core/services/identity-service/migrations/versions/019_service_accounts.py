"""PCI DSS Phase C: za_users gains is_service_account + account_type — a real
object-type flag distinguishing service/automation accounts from human users,
rather than a naming convention. Existing rows default to account_type='human',
is_service_account=false (no behavior change for anyone already provisioned).

Revision ID: 019
Revises: 018
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_users")}
    if "is_service_account" not in cols:
        op.add_column("za_users", sa.Column("is_service_account", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "account_type" not in cols:
        op.add_column("za_users", sa.Column("account_type", sa.String(20), nullable=False, server_default="human"))


def downgrade() -> None:
    op.drop_column("za_users", "account_type")
    op.drop_column("za_users", "is_service_account")
