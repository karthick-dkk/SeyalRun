"""Settings store: za_settings (superadmin-editable key/value, e.g. Zabbix URL)

Revision ID: 009
Revises: 008
Create Date: 2026-07-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "za_settings" not in set(inspector.get_table_names()):
        op.create_table(
            "za_settings",
            sa.Column("key", sa.String(100), primary_key=True),
            sa.Column("value", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table("za_settings")
