"""Configurable single-session and IP-restriction policies (v1.3): explicit
opt-in toggles (za_users.single_session_enabled, ip_restriction_enabled),
per-group equivalents live in the existing za_user_groups.policies JSON doc,
plus a new za_group_ip_restrictions table for group-shared CIDR lists.

Backfills ip_restriction_enabled=true for any existing user who already has a
non-empty allowed_ips list, so nobody's already-configured restriction is
silently dropped by the switch from implicit ("list non-empty") to explicit
enforcement.

Revision ID: 016
Revises: 015
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    user_cols = {c["name"] for c in inspector.get_columns("za_users")}
    if "ip_restriction_enabled" not in user_cols:
        op.add_column("za_users", sa.Column("ip_restriction_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        for uid, ips in bind.execute(sa.text("SELECT id, allowed_ips FROM za_users")).fetchall():
            if ips:
                bind.execute(
                    sa.text("UPDATE za_users SET ip_restriction_enabled = TRUE WHERE id = :uid"), {"uid": uid}
                )
    if "single_session_enabled" not in user_cols:
        op.add_column("za_users", sa.Column("single_session_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))

    if "za_group_ip_restrictions" not in set(inspector.get_table_names()):
        op.create_table(
            "za_group_ip_restrictions",
            sa.Column("group_id", sa.String(36), sa.ForeignKey("za_user_groups.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("cidrs", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table("za_group_ip_restrictions")
    op.drop_column("za_users", "single_session_enabled")
    op.drop_column("za_users", "ip_restriction_enabled")
