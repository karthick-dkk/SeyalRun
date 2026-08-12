"""In-UI notifications: za_notifications + za_notification_preferences.

Revision ID: auto_008
Revises: auto_007
Create Date: 2026-07-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "auto_008"
down_revision = "auto_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "za_notifications" not in table_names:
        op.create_table(
            "za_notifications",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), nullable=True),
            sa.Column("severity", sa.String(length=10), nullable=False, server_default="info"),
            sa.Column("title", sa.String(length=300), nullable=False),
            sa.Column("message", sa.Text(), nullable=False, server_default=""),
            sa.Column("source_type", sa.String(length=30), nullable=False, server_default="job_run"),
            sa.Column("source_id", sa.String(length=36), nullable=True),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_za_notifications_user_id", "za_notifications", ["user_id"])

    if "za_notification_preferences" not in table_names:
        op.create_table(
            "za_notification_preferences",
            sa.Column("user_id", sa.String(length=36), primary_key=True),
            sa.Column("muted_severities", sa.JSON(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    op.drop_table("za_notification_preferences")
    op.drop_index("ix_za_notifications_user_id", table_name="za_notifications")
    op.drop_table("za_notifications")
