"""initial automation schema

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

    if "za_projects" not in existing:
        op.create_table(
            "za_projects",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text, nullable=False, server_default=""),
            sa.Column("source_type", sa.String(20), nullable=False, server_default="local"),
            sa.Column("git_url", sa.String(500), nullable=False, server_default=""),
            sa.Column("git_branch", sa.String(100), nullable=False, server_default="main"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "za_job_templates" not in existing:
        op.create_table(
            "za_job_templates",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text, nullable=False, server_default=""),
            sa.Column("action_type", sa.String(30), nullable=False),
            sa.Column("playbook_path", sa.String(500), nullable=False, server_default=""),
            sa.Column("script_content", sa.Text, nullable=False, server_default=""),
            sa.Column("target_scope", sa.String(20), nullable=False, server_default="hosts"),
            sa.Column("target_host_ids", sa.JSON, nullable=False, server_default="[]"),
            sa.Column("target_host_group_id", sa.String(36), nullable=True),
            sa.Column("credential_id", sa.String(36), nullable=True),
            sa.Column("subject_credential_id", sa.String(36), nullable=True),
            sa.Column("survey_schema", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("default_params", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("quick_action", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["project_id"], ["za_projects.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_za_job_templates_project_id", "za_job_templates", ["project_id"])

    if "za_schedules" not in existing:
        op.create_table(
            "za_schedules",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("job_template_id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("cron_expression", sa.String(100), nullable=False),
            sa.Column("params_override", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["job_template_id"], ["za_job_templates.id"], ondelete="CASCADE"),
        )

    if "za_job_runs" not in existing:
        op.create_table(
            "za_job_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("job_template_id", sa.String(36), nullable=False),
            sa.Column("triggered_by", sa.String(100), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("params", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("output_lines", sa.JSON, nullable=False, server_default="[]"),
            sa.Column("exit_code", sa.Integer, nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["job_template_id"], ["za_job_templates.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_za_job_runs_job_template_id", "za_job_runs", ["job_template_id"])
        op.create_index("ix_za_job_runs_status", "za_job_runs", ["status"])

    if "za_secret_management_jobs" not in existing:
        op.create_table(
            "za_secret_management_jobs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("job_type", sa.String(20), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("subject_credential_id", sa.String(36), nullable=False),
            sa.Column("target_host_ids", sa.JSON, nullable=False, server_default="[]"),
            sa.Column("policy", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("schedule_id", sa.String(36), nullable=True),
            sa.Column("last_run_id", sa.String(36), nullable=True),
            sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table("za_secret_management_jobs")
    op.drop_table("za_job_runs")
    op.drop_table("za_schedules")
    op.drop_table("za_job_templates")
    op.drop_table("za_projects")
