"""Host tree view membership + self-service access requests.

* za_host_group_viewers — a user or user group may SEE the hosts under a host
  group (and its descendants). View only; kept out of za_authorizations so no
  login-type consumer can ever read it as access.
* za_authorizations gains request_kind / reason / request_duration_minutes for
  rules a user asked for from the Hosts page.

Revision ID: 024
Revises: 023
Create Date: 2026-11-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "za_host_group_viewers" not in set(insp.get_table_names()):
        op.create_table(
            "za_host_group_viewers",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("host_group_id", sa.String(36), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("za_users.id", ondelete="CASCADE"), nullable=True),
            sa.Column("user_group_id", sa.String(36), sa.ForeignKey("za_user_groups.id", ondelete="CASCADE"), nullable=True),
            sa.Column("created_by", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_za_host_group_viewers_host_group_id", "za_host_group_viewers", ["host_group_id"])
        op.create_index("ix_za_host_group_viewers_user_id", "za_host_group_viewers", ["user_id"])
        op.create_index("ix_za_host_group_viewers_user_group_id", "za_host_group_viewers", ["user_group_id"])

    cols = {c["name"] for c in insp.get_columns("za_authorizations")}
    if "request_kind" not in cols:
        op.add_column("za_authorizations", sa.Column("request_kind", sa.String(20), nullable=True))
    if "reason" not in cols:
        op.add_column("za_authorizations", sa.Column("reason", sa.Text(), nullable=True))
    if "request_duration_minutes" not in cols:
        op.add_column("za_authorizations", sa.Column("request_duration_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("za_authorizations", "request_duration_minutes")
    op.drop_column("za_authorizations", "reason")
    op.drop_column("za_authorizations", "request_kind")
    op.drop_table("za_host_group_viewers")
