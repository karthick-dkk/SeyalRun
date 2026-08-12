"""Zabbix user/group sync: za_user_groups.zabbix_usrgrpid links a group to the Zabbix
usergroup it mirrors. za_users.zabbix_userid already exists (added pre-v2); this only
adds the group-side column so usergroup sync can upsert by Zabbix id like host sync does.

Revision ID: 012
Revises: 011
Create Date: 2026-07-08
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
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_user_groups")}
    if "zabbix_usrgrpid" not in cols:
        op.add_column(
            "za_user_groups",
            sa.Column("zabbix_usrgrpid", sa.String(50), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("za_user_groups", "zabbix_usrgrpid")
