"""T17: read-only v_metrics_* views (schema contract for metrics-service)

metrics-service queries these views instead of raw tables, so a column rename
here is absorbed by updating the view rather than silently breaking the
dashboard. CREATE OR REPLACE VIEW is valid on both Postgres and MySQL.

Revision ID: inv_004
Revises: 003
Create Date: 2026-06-29
"""

from __future__ import annotations

from alembic import op

revision = "inv_004"
down_revision = "003"
branch_labels = None
depends_on = None

_VIEWS = {
    "v_metrics_hosts": "SELECT id, enabled, is_reachable FROM za_hosts",
    "v_metrics_credentials": "SELECT id, strength_score FROM za_credentials",
    "v_metrics_rotation_policies": "SELECT enabled, next_rotation_due FROM za_rotation_policies",
}


def upgrade() -> None:
    for name, body in _VIEWS.items():
        op.execute(f"CREATE OR REPLACE VIEW {name} AS {body}")


def downgrade() -> None:
    for name in _VIEWS:
        op.execute(f"DROP VIEW IF EXISTS {name}")
