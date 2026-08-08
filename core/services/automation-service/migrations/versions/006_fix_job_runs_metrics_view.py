"""Fix v_metrics_job_runs: add the missing id column

metrics-service's dashboard query selects r.id from this view (to build
Dashboard's "Recent Jobs" list and link each row to /jobs/{id}), but the view
created in 004 never exposed it — only status/started_at/triggered_by/
job_template_id. Every dashboard query against it raised "column r.id does
not exist", silently caught by main.py's broad except and surfaced as
recent_jobs: [] / top_playbooks: [] / degraded: ["jobs"] — real job runs
existed the whole time, the view just couldn't return them.

Revision ID: auto_006
Revises: auto_005
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op

revision = "auto_006"
down_revision = "auto_005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres's CREATE OR REPLACE VIEW can only append columns, never insert/reorder
    # them — putting id first would rename the existing column-1 "status" to "id"
    # instead of adding a new one. Appending it at the end is safe; the query that
    # reads this view selects columns by name, so order here doesn't matter.
    op.execute(
        "CREATE OR REPLACE VIEW v_metrics_job_runs AS "
        "SELECT status, started_at, triggered_by, job_template_id, id FROM za_job_runs"
    )


def downgrade() -> None:
    op.execute(
        "CREATE OR REPLACE VIEW v_metrics_job_runs AS "
        "SELECT status, started_at, triggered_by, job_template_id FROM za_job_runs"
    )
