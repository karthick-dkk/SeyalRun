"""T17: read-only v_metrics_* views (schema contract for metrics-service)

Revision ID: auto_004
Revises: 003
Create Date: 2026-06-29
"""

from __future__ import annotations

from alembic import op

revision = "auto_004"
down_revision = "003"
branch_labels = None
depends_on = None

_VIEWS = {
    "v_metrics_job_runs": "SELECT status, started_at, triggered_by, job_template_id FROM za_job_runs",
    "v_metrics_job_templates": "SELECT id, name FROM za_job_templates",
    "v_metrics_schedules": "SELECT name, next_run_at, enabled FROM za_schedules",
}


def upgrade() -> None:
    for name, body in _VIEWS.items():
        op.execute(f"CREATE OR REPLACE VIEW {name} AS {body}")


def downgrade() -> None:
    for name in _VIEWS:
        op.execute(f"DROP VIEW IF EXISTS {name}")
