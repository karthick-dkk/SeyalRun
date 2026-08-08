"""v1.1: authorizations many-to-many — array columns for multi-user + multi-host per rule

Revision ID: 005
Revises: 004
Create Date: 2026-06-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None

_COLS = ["user_ids", "user_group_ids", "host_ids", "host_group_ids"]
_SCALAR = {"user_ids": "user_id", "user_group_ids": "user_group_id",
           "host_ids": "host_id", "host_group_ids": "host_group_id"}


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_authorizations")}
    for col in _COLS:
        if col not in cols:
            op.add_column("za_authorizations", sa.Column(col, sa.JSON(), nullable=False, server_default="[]"))
    # Backfill arrays from existing single-value columns (postgres json_build_array).
    for arr, scalar in _SCALAR.items():
        op.execute(f"UPDATE za_authorizations SET {arr} = json_build_array({scalar}) WHERE {scalar} IS NOT NULL")


def downgrade() -> None:
    for col in _COLS:
        op.drop_column("za_authorizations", col)
