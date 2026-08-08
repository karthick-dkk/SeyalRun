"""Zone hierarchy (parent_zone_id): za_zones gains a self-referential parent
link, forming the multi-hop ProxyJump chain (ssh -J) — connecting to a host
in a nested zone walks its ancestor zones root-first, using each ancestor's
gateway as one hop.

Revision ID: 007
Revises: 006
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("za_zones")}
    if "parent_zone_id" not in cols:
        op.add_column("za_zones", sa.Column("parent_zone_id", sa.String(36), nullable=True))
        op.create_foreign_key(
            "fk_zone_parent", "za_zones", "za_zones", ["parent_zone_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    op.drop_constraint("fk_zone_parent", "za_zones", type_="foreignkey")
    op.drop_column("za_zones", "parent_zone_id")
