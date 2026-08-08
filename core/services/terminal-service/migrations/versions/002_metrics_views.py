"""T17: read-only v_metrics_* views (schema contract for metrics-service)

Revision ID: term_002
Revises: 001
Create Date: 2026-06-29
"""

from __future__ import annotations

from alembic import op

revision = "term_002"
down_revision = "001"
branch_labels = None
depends_on = None

_VIEWS = {
    "v_metrics_ssh_sessions": "SELECT id, status, started_at, username, host_name FROM za_ssh_sessions",
    "v_metrics_session_commands": "SELECT session_id, command_text, executed_at, action FROM za_session_commands",
}


def upgrade() -> None:
    for name, body in _VIEWS.items():
        op.execute(f"CREATE OR REPLACE VIEW {name} AS {body}")


def downgrade() -> None:
    for name in _VIEWS:
        op.execute(f"DROP VIEW IF EXISTS {name}")
