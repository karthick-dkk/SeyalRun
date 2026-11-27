"""Host tree: host groups may be nested.

za_host_groups gains a nullable self-reference, parent_id. Every existing group
stays a root (NULL), so nothing a deployment grants today changes meaning: a
grant on a group covers its own hosts plus the hosts of groups below it, and
until somebody gives a group a parent there is nothing below anything.

ON DELETE SET NULL rather than CASCADE — removing a parent outside the API
detaches its children (narrowing what a grant on the parent covers) instead of
deleting them. The API refuses to delete a group that still has children.

Revision ID: 014
Revises: 013
Create Date: 2026-11-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None

_FK = "fk_za_host_groups_parent_id"
_IX = "ix_za_host_groups_parent_id"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("za_host_groups")}
    if "parent_id" not in cols:
        op.add_column("za_host_groups", sa.Column("parent_id", sa.String(36), nullable=True))
    if _FK not in {fk.get("name") for fk in insp.get_foreign_keys("za_host_groups")}:
        op.create_foreign_key(_FK, "za_host_groups", "za_host_groups", ["parent_id"], ["id"], ondelete="SET NULL")
    if _IX not in {ix.get("name") for ix in insp.get_indexes("za_host_groups")}:
        op.create_index(_IX, "za_host_groups", ["parent_id"])


def downgrade() -> None:
    op.drop_index(_IX, table_name="za_host_groups")
    op.drop_constraint(_FK, "za_host_groups", type_="foreignkey")
    op.drop_column("za_host_groups", "parent_id")
