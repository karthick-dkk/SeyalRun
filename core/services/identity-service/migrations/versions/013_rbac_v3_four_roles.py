"""RBAC v3: exactly 4 roles — superadmin, admin, support, user — reseeded from
libs.rbaccore.BUILTIN_ROLE_PERMS. Removes automation/audit/guest entirely (confirmed
zero za_user_roles/za_group_roles references before this migration was written).

Revision ID: 013
Revises: 012
Create Date: 2026-07-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from libs.rbaccore import BUILTIN_ROLE_PERMS

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None

_REMOVED_ROLES = ("automation", "audit", "guest")


def upgrade() -> None:
    # Reseed the 4 kept roles' permission docs. Pass the dict directly — the JSON
    # column serializes it to a JSON object; json.dumps() would double-encode it
    # into a JSON string scalar (see migration 011).
    roles = sa.table("za_roles", sa.column("name", sa.String), sa.column("permissions", sa.JSON))
    for name, doc in BUILTIN_ROLE_PERMS.items():
        op.execute(roles.update().where(roles.c.name == name).values(permissions=doc))

    # Drop the retired built-ins. FK ondelete=CASCADE on za_user_roles/za_group_roles
    # cleans up any stray grant automatically; this is safe only because no live
    # account currently holds these roles (verified before writing this migration).
    op.execute(roles.delete().where(roles.c.name.in_(_REMOVED_ROLES)))


def downgrade() -> None:
    # Recreate the retired roles as empty/no-access docs — their original permission
    # shape isn't recoverable from this migration alone.
    roles = sa.table("za_roles", sa.column("id", sa.String), sa.column("name", sa.String),
                     sa.column("permissions", sa.JSON), sa.column("is_builtin", sa.Boolean))
    import uuid
    for name in _REMOVED_ROLES:
        op.execute(
            roles.insert().values(
                id=str(uuid.uuid4()), name=name, permissions={"flags": [], "perms": {}}, is_builtin=True
            )
        )
