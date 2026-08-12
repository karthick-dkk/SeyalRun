"""initial inventory schema

Revision ID: 001
Revises:
Create Date: 2026-06-10

Mirrors schema/{postgres,mysql}/schema.sql inventory DB tables. Guarded with
``has_table`` checks so this is a no-op (beyond stamping) when
``ops/init-db.sh`` already imported schema.sql against a bare-metal DB.
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
    existing = set(inspector.get_table_names())

    if "za_zones" not in existing:
        op.create_table(
            "za_zones",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "za_gateways" not in existing:
        op.create_table(
            "za_gateways",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("zone_id", sa.String(36), sa.ForeignKey("za_zones.id", ondelete="CASCADE"), nullable=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("host", sa.String(255), nullable=False),
            sa.Column("port", sa.Integer(), nullable=False, server_default="22"),
            sa.Column("username", sa.String(100), nullable=False, server_default=""),
            sa.Column("credential_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "za_host_groups" not in existing:
        op.create_table(
            "za_host_groups",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "za_hosts" not in existing:
        op.create_table(
            "za_hosts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("zabbix_hostid", sa.String(50), nullable=True, unique=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("ip", sa.String(100), nullable=False),
            sa.Column("port", sa.Integer(), nullable=False, server_default="22"),
            sa.Column("os_type", sa.String(20), nullable=False, server_default="linux"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("zone_id", sa.String(36), sa.ForeignKey("za_zones.id", ondelete="SET NULL"), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_reachable", sa.Boolean(), nullable=True),
            sa.Column("last_ping_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "za_host_group_members" not in existing:
        op.create_table(
            "za_host_group_members",
            sa.Column("host_id", sa.String(36), sa.ForeignKey("za_hosts.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("group_id", sa.String(36), sa.ForeignKey("za_host_groups.id", ondelete="CASCADE"), primary_key=True),
        )

    if "za_credential_templates" not in existing:
        op.create_table(
            "za_credential_templates",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False, unique=True),
            sa.Column("secret_type", sa.String(20), nullable=False, server_default="password"),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("default_username", sa.String(100), nullable=False, server_default=""),
            sa.Column("default_params", sa.JSON(), nullable=False),
            sa.Column("push_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("rotation_days", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "za_credentials" not in existing:
        op.create_table(
            "za_credentials",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False, server_default=""),
            sa.Column("template_id", sa.String(36), sa.ForeignKey("za_credential_templates.id", ondelete="SET NULL"), nullable=True),
            sa.Column("username", sa.String(100), nullable=False),
            sa.Column("secret_type", sa.String(20), nullable=False, server_default="password"),
            sa.Column("secret_ciphertext", sa.Text(), nullable=False),
            sa.Column("credential_scope", sa.String(20), nullable=False, server_default="host"),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "za_credential_host_links" not in existing:
        op.create_table(
            "za_credential_host_links",
            sa.Column("credential_id", sa.String(36), sa.ForeignKey("za_credentials.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("host_id", sa.String(36), sa.ForeignKey("za_hosts.id", ondelete="CASCADE"), primary_key=True),
        )


def downgrade() -> None:
    for table in (
        "za_credential_host_links", "za_credentials", "za_credential_templates",
        "za_host_group_members", "za_hosts", "za_host_groups", "za_gateways", "za_zones",
    ):
        op.drop_table(table)
