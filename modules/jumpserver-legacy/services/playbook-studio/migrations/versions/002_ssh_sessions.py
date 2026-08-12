"""Add ssh_sessions table.

Revision ID: 002
Revises: 001
Create Date: 2026-06-01 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ssh_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user", sa.String(100), nullable=False),
        sa.Column("asset_id", sa.String(100), nullable=True),
        sa.Column("asset_name", sa.String(200), nullable=True, server_default=""),
        sa.Column("asset_address", sa.String(255), nullable=False),
        sa.Column("ssh_username", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("recording", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("command_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exit_reason", sa.String(200), nullable=True, server_default=""),
        sa.PrimaryKeyConstraint("id"),
        schema="playbook_studio",
    )
    op.create_index("ix_ssh_sessions_user", "ssh_sessions", ["user"], schema="playbook_studio")
    op.create_index("ix_ssh_sessions_status", "ssh_sessions", ["status"], schema="playbook_studio")
    op.create_index(
        "ix_ssh_sessions_started_at", "ssh_sessions", ["started_at"], schema="playbook_studio"
    )


def downgrade() -> None:
    op.drop_table("ssh_sessions", schema="playbook_studio")
