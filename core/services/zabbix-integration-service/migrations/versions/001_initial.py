"""initial zabbix integration schema

Revision ID: 001
Revises:
Create Date: 2026-06-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = inspector.get_table_names()

    if "za_zabbix_trigger_bindings" not in existing:
        op.create_table(
            "za_zabbix_trigger_bindings",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("job_template_id", sa.String(36), nullable=False),
            sa.Column("zabbix_triggerid", sa.String(50), nullable=True),
            sa.Column("zabbix_host_group", sa.String(200), nullable=True),
            sa.Column("severity_min", sa.Integer, nullable=False, server_default="0"),
            sa.Column("target_scope", sa.String(20), nullable=False, server_default="affected_host"),
            sa.Column("post_result_to_zabbix", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("extra_vars", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table("za_zabbix_trigger_bindings")
