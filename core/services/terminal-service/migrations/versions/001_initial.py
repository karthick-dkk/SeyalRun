"""initial terminal schema

Revision ID: 001
Revises:
Create Date: 2025-01-01 00:00:00.000000
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

    if "za_ssh_sessions" not in existing:
        op.create_table(
            "za_ssh_sessions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("username", sa.String(100), nullable=False, server_default=""),
            sa.Column("host_id", sa.String(36), nullable=False),
            sa.Column("host_name", sa.String(200), nullable=False, server_default=""),
            sa.Column("credential_id", sa.String(36), nullable=False),
            sa.Column("gateway_id", sa.String(36), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("client_ip", sa.String(64), nullable=False, server_default=""),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        )
        op.create_index("ix_za_ssh_sessions_user_id", "za_ssh_sessions", ["user_id"])
        op.create_index("ix_za_ssh_sessions_host_id", "za_ssh_sessions", ["host_id"])

    if "za_session_commands" not in existing:
        op.create_table(
            "za_session_commands",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("session_id", sa.String(36), sa.ForeignKey("za_ssh_sessions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("command_text", sa.Text(), nullable=False),
            sa.Column("matched_filter_id", sa.String(36), nullable=True),
            sa.Column("action", sa.String(20), nullable=False, server_default="logged"),
            sa.Column("executed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_za_session_commands_session_id", "za_session_commands", ["session_id"])


def downgrade() -> None:
    op.drop_table("za_session_commands")
    op.drop_table("za_ssh_sessions")
