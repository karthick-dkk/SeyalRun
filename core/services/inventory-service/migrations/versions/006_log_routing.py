"""Log backend content routing: category -> [backends]

Revision ID: 006
Revises: 005
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_log_backend_config")}
    if "routing" not in cols:
        op.add_column(
            "za_log_backend_config",
            sa.Column("routing", sa.JSON(), nullable=False, server_default="{}"),
        )


def downgrade() -> None:
    op.drop_column("za_log_backend_config", "routing")
