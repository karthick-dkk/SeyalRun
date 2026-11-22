"""SeyalRun 3.0 posture: grant the new "posture" segment on the STORED built-in
role docs (admin: all methods, support: GET).

libs.rbaccore.BUILTIN_ROLE_PERMS is only the identity-down fallback; api-gateway
enforces the DB-stored ZARole.permissions doc. Without this, every existing
deployment's admin and support roles are refused /posture/* (only superadmin
passes, on all=True). Same update-in-place style as 020_access_review. Only adds
the key when absent, so an admin who already edited it keeps their choice.

Revision ID: 023
Revises: 022
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None

_GRANTS = {
    "admin": ["GET", "POST", "PUT", "PATCH", "DELETE"],
    "support": ["GET"],
}


def upgrade() -> None:
    bind = op.get_bind()
    roles = sa.table("za_roles", sa.column("name", sa.String), sa.column("permissions", sa.JSON))
    for name, doc in bind.execute(sa.select(roles.c.name, roles.c.permissions)).fetchall():
        if name not in _GRANTS:
            continue
        new_doc = dict(doc or {})
        perms = dict(new_doc.get("perms") or {})
        if "posture" in perms:
            continue
        perms["posture"] = list(_GRANTS[name])
        new_doc["perms"] = perms
        op.execute(roles.update().where(roles.c.name == name).values(permissions=new_doc))


def downgrade() -> None:
    bind = op.get_bind()
    roles = sa.table("za_roles", sa.column("name", sa.String), sa.column("permissions", sa.JSON))
    for name, doc in bind.execute(sa.select(roles.c.name, roles.c.permissions)).fetchall():
        perms = dict((doc or {}).get("perms") or {})
        if name in _GRANTS and "posture" in perms:
            perms.pop("posture")
            op.execute(roles.update().where(roles.c.name == name).values(permissions={**doc, "perms": perms}))
