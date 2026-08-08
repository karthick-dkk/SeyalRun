"""add zabbix_trigger_name to trigger bindings

Revision ID: 002
Revises: 001
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("za_zabbix_trigger_bindings")]

    if "zabbix_trigger_name" not in columns:
        op.add_column(
            "za_zabbix_trigger_bindings",
            sa.Column("zabbix_trigger_name", sa.String(255), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("za_zabbix_trigger_bindings", "zabbix_trigger_name")
