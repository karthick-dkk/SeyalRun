"""PCI DSS Phase B (10.5.1): za_log_backend_config gains a retention JSON doc,
admin-editable alongside routing instead of retention living only as a
hardcoded .env global (AUDIT_LOG_RETENTION_DAYS).

Revision ID: 008
Revises: 007
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_log_backend_config")}
    if "retention" not in cols:
        op.add_column("za_log_backend_config", sa.Column("retention", sa.JSON(), nullable=True, server_default="{}"))


def downgrade() -> None:
    op.drop_column("za_log_backend_config", "retention")
