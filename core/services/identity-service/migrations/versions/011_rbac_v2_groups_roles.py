"""RBAC v2: group→role grants (za_group_roles) + reseed built-in role perms to the
per-verb capability shape from libs.rbaccore. Legacy za_users.role_id and the singular
za_authorizations FK columns are intentionally KEPT (nullable mirrors) — effective_roles
is authoritative; dropping the columns is deferred (high blast radius, no user value).

Revision ID: 011
Revises: 010
Create Date: 2026-07-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from libs.rbaccore import BUILTIN_ROLE_PERMS

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "za_group_roles" not in set(inspector.get_table_names()):
        op.create_table(
            "za_group_roles",
            sa.Column("group_id", sa.String(36),
                      sa.ForeignKey("za_user_groups.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("role_id", sa.String(36),
                      sa.ForeignKey("za_roles.id", ondelete="CASCADE"), primary_key=True),
        )

    # Reseed built-in role permission docs to the v2 per-verb capability shape.
    # JSON column: pass a JSON string; both Postgres JSON and MySQL JSON accept it.
    # Pass the dict directly — the JSON column serializes it to a JSON object.
    # (json.dumps() would double-encode it into a JSON string scalar.)
    roles = sa.table("za_roles", sa.column("name", sa.String), sa.column("permissions", sa.JSON))
    for name, doc in BUILTIN_ROLE_PERMS.items():
        op.execute(roles.update().where(roles.c.name == name).values(permissions=doc))

    # Backfill: effective_roles reads za_user_roles authoritatively. Any pre-v2 user who
    # was seeded/assigned via the legacy role_id mirror needs a matching za_user_roles row,
    # else they resolve to no roles and are locked out. Idempotent (skip existing rows).
    op.execute(
        """
        INSERT INTO za_user_roles (user_id, role_id)
        SELECT u.id, u.role_id FROM za_users u
        WHERE u.role_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM za_user_roles ur
            WHERE ur.user_id = u.id AND ur.role_id = u.role_id
          )
        """
    )


def downgrade() -> None:
    op.drop_table("za_group_roles")
