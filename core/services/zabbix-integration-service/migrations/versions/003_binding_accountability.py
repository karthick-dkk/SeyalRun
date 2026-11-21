"""Trigger bindings record who created and last changed them.

A binding decides which automation runs when a Zabbix problem fires. When that
run targets a production-tagged host it is held for approval, and its
triggered_by names the Zabbix event, not the superadmin who wrote the binding.
The binding's author and last editor are sent with each dispatch so
automation-service can refuse their approval of a run their own binding
produced (separation of duties, F-2).

Nullable: bindings created before this have no recorded author; re-saving one
records the editor.

Revision ID: 003
Revises: 002
Create Date: 2026-11-21
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("za_zabbix_trigger_bindings")}
    if "created_by" not in columns:
        op.add_column("za_zabbix_trigger_bindings", sa.Column("created_by", sa.String(36), nullable=True))
    if "updated_by" not in columns:
        op.add_column("za_zabbix_trigger_bindings", sa.Column("updated_by", sa.String(36), nullable=True))


def downgrade() -> None:
    op.drop_column("za_zabbix_trigger_bindings", "updated_by")
    op.drop_column("za_zabbix_trigger_bindings", "created_by")
