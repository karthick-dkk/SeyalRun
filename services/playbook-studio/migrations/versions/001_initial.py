"""Initial schema — playbooks, jobs, alert_rules, alert_history, notification_channels.

Revision ID: 001
Revises:
Create Date: 2026-01-01 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS playbook_studio")

    op.create_table(
        "playbooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("tags", postgresql.JSONB(), nullable=True),
        sa.Column("tasks", postgresql.JSONB(), nullable=True),
        sa.Column("variables", postgresql.JSONB(), nullable=True),
        sa.Column("yaml_cache", sa.Text(), nullable=True),
        sa.Column("is_template", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("source_template_id", sa.String(255), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        schema="playbook_studio",
    )
    op.create_index("ix_playbooks_category", "playbooks", ["category"], schema="playbook_studio")
    op.create_index(
        "ix_playbooks_is_template", "playbooks", ["is_template"], schema="playbook_studio"
    )
    op.create_index(
        "ix_playbooks_updated_at", "playbooks", ["updated_at"], schema="playbook_studio"
    )

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("playbook_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("triggered_by", sa.String(255), nullable=True),
        sa.Column("ab_job_id", sa.String(255), nullable=True),
        sa.Column("inventory_selector", sa.String(255), nullable=True),
        sa.Column("extra_vars", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("output_lines", postgresql.JSONB(), nullable=True),
        sa.Column("yaml_content", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(
            ["playbook_id"], ["playbook_studio.playbooks.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="playbook_studio",
    )
    op.create_index("ix_jobs_playbook_id", "jobs", ["playbook_id"], schema="playbook_studio")
    op.create_index("ix_jobs_status", "jobs", ["status"], schema="playbook_studio")
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"], schema="playbook_studio")

    op.create_table(
        "alert_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("conditions", postgresql.JSONB(), nullable=True),
        sa.Column("channels", postgresql.JSONB(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        schema="playbook_studio",
    )
    op.create_index(
        "ix_alert_rules_event_type", "alert_rules", ["event_type"], schema="playbook_studio"
    )
    op.create_index("ix_alert_rules_enabled", "alert_rules", ["enabled"], schema="playbook_studio")

    op.create_table(
        "alert_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rule_name", sa.String(255), nullable=True),
        sa.Column("event_type", sa.String(100), nullable=True),
        sa.Column("event_payload", postgresql.JSONB(), nullable=True),
        sa.Column("channels_tried", postgresql.JSONB(), nullable=True),
        sa.Column("delivery_status", sa.String(50), nullable=True),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["rule_id"], ["playbook_studio.alert_rules.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="playbook_studio",
    )
    op.create_index(
        "ix_alert_history_rule_id", "alert_history", ["rule_id"], schema="playbook_studio"
    )
    op.create_index(
        "ix_alert_history_delivered_at", "alert_history", ["delivered_at"], schema="playbook_studio"
    )

    op.create_table(
        "notification_channels",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("channel_type", sa.String(50), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        schema="playbook_studio",
    )


def downgrade() -> None:
    op.drop_table("notification_channels", schema="playbook_studio")
    op.drop_table("alert_history", schema="playbook_studio")
    op.drop_table("alert_rules", schema="playbook_studio")
    op.drop_table("jobs", schema="playbook_studio")
    op.drop_table("playbooks", schema="playbook_studio")
    op.execute("DROP SCHEMA IF EXISTS playbook_studio")
