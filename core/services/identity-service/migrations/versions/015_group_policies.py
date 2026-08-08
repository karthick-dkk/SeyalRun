"""Group policies (v1.2): za_user_groups.policies, za_users.setup_completed
(backfilled true for existing users) + allowed_ips, za_group_notify_config table.

Revision ID: 015
Revises: 014
Create Date: 2026-07-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    group_cols = {c["name"] for c in inspector.get_columns("za_user_groups")}
    if "policies" not in group_cols:
        op.add_column("za_user_groups", sa.Column("policies", sa.JSON(), nullable=True))
        op.execute(sa.table("za_user_groups", sa.column("policies", sa.JSON())).update().values(policies={}))

    user_cols = {c["name"] for c in inspector.get_columns("za_users")}
    if "setup_completed" not in user_cols:
        op.add_column("za_users", sa.Column("setup_completed", sa.Boolean(), nullable=False, server_default=sa.false()))
        # Existing accounts are not "new" — only users created after this ships (default
        # false) should ever see the setup wizard.
        op.execute(sa.table("za_users", sa.column("setup_completed", sa.Boolean())).update().values(setup_completed=True))
    if "allowed_ips" not in user_cols:
        op.add_column("za_users", sa.Column("allowed_ips", sa.JSON(), nullable=True))
        op.execute(sa.table("za_users", sa.column("allowed_ips", sa.JSON())).update().values(allowed_ips=[]))

    if "za_group_notify_config" not in set(inspector.get_table_names()):
        op.create_table(
            "za_group_notify_config",
            sa.Column("group_id", sa.String(36), sa.ForeignKey("za_user_groups.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("emails", sa.JSON(), nullable=False),
            sa.Column("min_severity", sa.String(20), nullable=False, server_default="medium"),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table("za_group_notify_config")
    op.drop_column("za_users", "allowed_ips")
    op.drop_column("za_users", "setup_completed")
    op.drop_column("za_user_groups", "policies")
