"""Log backend: es_verify_ssl toggle (allow self-signed Elasticsearch)

Revision ID: 005
Revises: inv_004
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "inv_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_log_backend_config")}
    if "es_verify_ssl" not in cols:
        op.add_column(
            "za_log_backend_config",
            sa.Column("es_verify_ssl", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    op.drop_column("za_log_backend_config", "es_verify_ssl")
