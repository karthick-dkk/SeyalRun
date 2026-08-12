"""v1.1 RBAC custom roles: is_builtin flag + canonical {all,read,write,reveal} permissions

Revision ID: 004
Revises: 003
Create Date: 2026-06-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None

# Canonical permission shape consumed by the gateway:
#   {"all": bool, "reveal": bool, "read": [segments|"*"], "write": [segments]}
_PERMS = {
    "superadmin": {"all": True, "reveal": True},
    "admin": {"reveal": True,
              "write": ["users", "authorizations", "api-tokens", "roles", "hosts", "host-groups",
                        "credentials", "credential-templates", "zones", "projects", "job-templates",
                        "schedules", "job-runs", "secret-management-jobs", "trigger-bindings",
                        "triggers", "ssh", "recordings"],
              "read": ["metrics"]},
    "automation": {"write": ["projects", "job-templates", "schedules", "job-runs",
                             "secret-management-jobs", "trigger-bindings", "triggers"],
                   "read": ["hosts", "host-groups", "credentials", "credential-templates",
                            "zones", "metrics", "recordings"]},
    "support": {"read": ["hosts", "host-groups", "users", "roles", "projects", "job-templates",
                         "schedules", "job-runs", "metrics"]},
    "audit": {"read": ["*"]},
    "guest": {"write": ["ssh"], "read": ["hosts", "recordings", "metrics"]},
    "user": {"write": ["ssh"], "read": ["hosts", "recordings", "metrics"]},
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("za_roles")}
    if "is_builtin" not in cols:
        op.add_column("za_roles", sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()))

    roles = sa.table("za_roles",
                     sa.column("name", sa.String),
                     sa.column("permissions", sa.JSON),
                     sa.column("is_builtin", sa.Boolean))
    for name, perms in _PERMS.items():
        op.execute(roles.update().where(roles.c.name == name).values(permissions=perms, is_builtin=True))


def downgrade() -> None:
    op.drop_column("za_roles", "is_builtin")
