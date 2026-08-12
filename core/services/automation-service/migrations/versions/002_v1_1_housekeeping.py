"""v1.1: housekeeping jobs table + host_results column on za_job_runs

Revision ID: 002
Revises: 001
Create Date: 2026-06-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

_DEFAULT_JOBS = [
    ("session_recording_purge",      "Session Recording Purge",       "Purge frames from recordings older than RECORDING_RETENTION_DAYS",             "0 2 * * *"),
    ("session_recording_tier",       "Session Recording Tier",        "Move local recordings older than RECORDING_TIER_AFTER_DAYS to S3/ES",          "0 * * * *"),
    ("log_backend_replay",           "Log Backend Replay",            "Re-post failed audit entries to the configured ES/S3 log backend",             "*/5 * * * *"),
    ("weak_credential_scan",         "Weak Credential Scan",          "Scan all credentials for zxcvbn strength score below threshold",               "0 3 * * *"),
    ("password_rotation_due_check",  "Password Rotation Due Check",   "Trigger rotation for credentials with overdue rotation policies",              "0 4 * * *"),
    ("asset_reachability_test",      "Asset Reachability Test",       "Ping/probe all enabled hosts and update reachability status",                   "*/15 * * * *"),
    ("orphan_job_logs_cleanup",      "Orphan Job Logs Cleanup",       "Delete job runs whose job template no longer exists",                           "0 5 * * 0"),
    ("elasticsearch_index_rollover", "Elasticsearch Index Rollover",  "Call ES _rollover API on audit/log indices to enforce size/age limits",         "0 1 * * *"),
    ("audit_log_archive",            "Audit Log Archive",             "Archive audit log entries older than AUDIT_LOG_RETENTION_DAYS to cold storage", "0 5 * * *"),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # ── za_housekeeping_jobs ────────────────────────────────────────────────
    if "za_housekeeping_jobs" not in existing_tables:
        op.create_table(
            "za_housekeeping_jobs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("job_key", sa.String(100), nullable=False, unique=True),
            sa.Column("display_name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("cron_expression", sa.String(100), nullable=False),
            sa.Column("cron_override", sa.String(100), nullable=True),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_run_status", sa.String(20), nullable=False, server_default=""),
            sa.Column("last_run_error", sa.Text(), nullable=False, server_default=""),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("manual_trigger", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        # Seed default jobs
        import uuid
        hj = sa.table(
            "za_housekeeping_jobs",
            sa.column("id", sa.String),
            sa.column("job_key", sa.String),
            sa.column("display_name", sa.String),
            sa.column("description", sa.Text),
            sa.column("cron_expression", sa.String),
        )
        op.bulk_insert(hj, [
            {"id": str(uuid.uuid4()), "job_key": key, "display_name": name, "description": desc, "cron_expression": cron}
            for key, name, desc, cron in _DEFAULT_JOBS
        ])

    # ── host_results column on za_job_runs ──────────────────────────────────
    if "za_job_runs" in existing_tables:
        existing_cols = {c["name"] for c in inspector.get_columns("za_job_runs")}
        if "host_results" not in existing_cols:
            op.add_column("za_job_runs", sa.Column(
                "host_results", sa.JSON(), nullable=False, server_default="{}"
            ))


def downgrade() -> None:
    op.drop_column("za_job_runs", "host_results")
    op.drop_table("za_housekeeping_jobs")
