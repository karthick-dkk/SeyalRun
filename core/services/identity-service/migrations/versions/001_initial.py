"""initial identity schema

Revision ID: 001
Revises:
Create Date: 2026-06-10

Mirrors schema/{postgres,mysql}/schema.sql identity DB tables. Guarded with
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

    if "za_roles" not in existing:
        op.create_table(
            "za_roles",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(50), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("permissions", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "za_user_groups" not in existing:
        op.create_table(
            "za_user_groups",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "za_users" not in existing:
        op.create_table(
            "za_users",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("username", sa.String(100), nullable=False, unique=True),
            sa.Column("display_name", sa.String(200), nullable=False, server_default=""),
            sa.Column("email", sa.String(200), nullable=False, server_default=""),
            sa.Column("zabbix_userid", sa.String(50), nullable=True),
            sa.Column("role_id", sa.String(36), sa.ForeignKey("za_roles.id", ondelete="SET NULL"), nullable=True),
            sa.Column("password_hash", sa.String(255), nullable=False, server_default=""),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "za_user_group_members" not in existing:
        op.create_table(
            "za_user_group_members",
            sa.Column("user_id", sa.String(36), sa.ForeignKey("za_users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("group_id", sa.String(36), sa.ForeignKey("za_user_groups.id", ondelete="CASCADE"), primary_key=True),
        )

    if "za_sessions" not in existing:
        op.create_table(
            "za_sessions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("za_users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("jwt_id", sa.String(36), nullable=False, unique=True),
            sa.Column("ip_address", sa.String(64), nullable=False, server_default=""),
            sa.Column("user_agent", sa.String(255), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_za_sessions_user_id", "za_sessions", ["user_id"])

    if "za_authorizations" not in existing:
        op.create_table(
            "za_authorizations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("za_users.id", ondelete="CASCADE"), nullable=True),
            sa.Column("user_group_id", sa.String(36), sa.ForeignKey("za_user_groups.id", ondelete="CASCADE"), nullable=True),
            sa.Column("host_id", sa.String(36), nullable=True),
            sa.Column("host_group_id", sa.String(36), nullable=True),
            sa.Column("credential_id", sa.String(36), nullable=True),
            sa.Column("actions", sa.JSON(), nullable=False),
            sa.Column("date_start", sa.DateTime(timezone=True), nullable=True),
            sa.Column("date_expired", sa.DateTime(timezone=True), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_za_authz_user_id", "za_authorizations", ["user_id"])
        op.create_index("ix_za_authz_user_group_id", "za_authorizations", ["user_group_id"])
        op.create_index("ix_za_authz_host_id", "za_authorizations", ["host_id"])
        op.create_index("ix_za_authz_host_group_id", "za_authorizations", ["host_group_id"])

    if "za_command_groups" not in existing:
        op.create_table(
            "za_command_groups",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("patterns", sa.JSON(), nullable=False),
            sa.Column("match_type", sa.String(20), nullable=False, server_default="regex"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "za_command_filters" not in existing:
        op.create_table(
            "za_command_filters",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("command_group_id", sa.String(36), sa.ForeignKey("za_command_groups.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("za_users.id", ondelete="CASCADE"), nullable=True),
            sa.Column("user_group_id", sa.String(36), sa.ForeignKey("za_user_groups.id", ondelete="CASCADE"), nullable=True),
            sa.Column("host_id", sa.String(36), nullable=True),
            sa.Column("host_group_id", sa.String(36), nullable=True),
            sa.Column("action", sa.String(20), nullable=False, server_default="deny"),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_za_cmdfilter_group", "za_command_filters", ["command_group_id"])

    if "za_login_acls" not in existing:
        op.create_table(
            "za_login_acls",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("za_users.id", ondelete="CASCADE"), nullable=True),
            sa.Column("user_group_id", sa.String(36), sa.ForeignKey("za_user_groups.id", ondelete="CASCADE"), nullable=True),
            sa.Column("ip_cidr", sa.String(64), nullable=True),
            sa.Column("time_start", sa.String(5), nullable=True),
            sa.Column("time_end", sa.String(5), nullable=True),
            sa.Column("days_of_week", sa.JSON(), nullable=False),
            sa.Column("action", sa.String(10), nullable=False, server_default="allow"),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "za_api_tokens" not in existing:
        op.create_table(
            "za_api_tokens",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("za_users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("token_hash", sa.String(255), nullable=False),
            sa.Column("token_prefix", sa.String(12), nullable=False),
            sa.Column("scopes", sa.JSON(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_za_api_tokens_user_id", "za_api_tokens", ["user_id"])
        op.create_index("ix_za_api_tokens_prefix", "za_api_tokens", ["token_prefix"])

    if "za_audit_logs" not in existing:
        op.create_table(
            "za_audit_logs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), nullable=True),
            sa.Column("username", sa.String(100), nullable=False, server_default=""),
            sa.Column("action", sa.String(100), nullable=False),
            sa.Column("resource_type", sa.String(50), nullable=False, server_default=""),
            sa.Column("resource_id", sa.String(36), nullable=False, server_default=""),
            sa.Column("details", sa.JSON(), nullable=False),
            sa.Column("ip_address", sa.String(64), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_za_audit_created_at", "za_audit_logs", ["created_at"])
        op.create_index("ix_za_audit_user_id", "za_audit_logs", ["user_id"])

    # Seed default roles (idempotent — primary key conflict is ignored).
    roles = sa.table(
        "za_roles",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("permissions", sa.JSON),
    )
    for row in (
        {"id": "00000000-0000-0000-0000-000000000001", "name": "superadmin",
         "description": "Full access, including Settings/Integrations", "permissions": ["*"]},
        {"id": "00000000-0000-0000-0000-000000000002", "name": "admin",
         "description": "Manage assets, credentials, automation", "permissions": ["assets:*", "credentials:*", "automation:*"]},
        {"id": "00000000-0000-0000-0000-000000000003", "name": "user",
         "description": "Workbench access to authorized hosts only", "permissions": ["workbench:use"]},
    ):
        existing_role = bind.execute(sa.text("SELECT id FROM za_roles WHERE id = :id"), {"id": row["id"]}).first()
        if existing_role is None:
            bind.execute(roles.insert().values(**row))


def downgrade() -> None:
    for table in (
        "za_audit_logs", "za_api_tokens", "za_login_acls", "za_command_filters",
        "za_command_groups", "za_authorizations", "za_sessions",
        "za_user_group_members", "za_users", "za_user_groups", "za_roles",
    ):
        op.drop_table(table)
