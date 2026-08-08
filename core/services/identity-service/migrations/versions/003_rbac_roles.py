"""v1.1 RBAC: za_user_roles join table + seed 6 default roles + migrate existing role_id

Revision ID: 003
Revises: 002
Create Date: 2026-06-23
"""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None

# name, description, permissions (informational — gateway is the enforcement point)
_ROLES = [
    ("superadmin", "Full access to everything, including housekeeping, audit, log backend and security.",
     {"all": True}),
    ("admin", "Manage everything except housekeeping, audit, log backend and security policies.",
     {"manage": ["users", "authorizations", "hosts", "host-groups", "credentials", "zones",
                 "automation", "tokens", "recordings", "terminal"],
      "deny": ["housekeeping", "audit", "log-backend", "security"]}),
    ("automation", "Full read/write on automation (projects, job templates, schedules, runs, triggers).",
     {"manage": ["automation"], "read": ["hosts", "credentials", "zones", "dashboard"]}),
    ("support", "Read-only on hosts, automation, dashboard, users, groups and host groups.",
     {"read": ["hosts", "host-groups", "users", "user-groups", "automation", "dashboard"]}),
    ("audit", "Complete read-only access; cannot edit anything or reveal secrets.",
     {"read": ["*"], "deny": ["credential-reveal"]}),
    ("guest", "Only authorized hosts + SSH terminal; cannot view passwords.",
     {"read": ["hosts", "recordings", "dashboard"], "use": ["terminal"], "deny": ["credentials"]}),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # ── za_user_roles (many-to-many) ────────────────────────────────────────
    if "za_user_roles" not in tables:
        op.create_table(
            "za_user_roles",
            sa.Column("user_id", sa.String(36), sa.ForeignKey("za_users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("role_id", sa.String(36), sa.ForeignKey("za_roles.id", ondelete="CASCADE"), primary_key=True),
        )

    roles_tbl = sa.table(
        "za_roles",
        sa.column("id", sa.String), sa.column("name", sa.String),
        sa.column("description", sa.Text), sa.column("permissions", sa.JSON),
    )
    user_roles_tbl = sa.table(
        "za_user_roles", sa.column("user_id", sa.String), sa.column("role_id", sa.String),
    )

    # ── Seed the 6 default roles (idempotent by name) ───────────────────────
    for name, desc, perms in _ROLES:
        row = bind.execute(sa.text("SELECT id FROM za_roles WHERE name=:n"), {"n": name}).fetchone()
        if not row:
            op.bulk_insert(roles_tbl, [{"id": str(uuid.uuid4()), "name": name, "description": desc, "permissions": perms}])

    # ── Migrate existing single role_id -> za_user_roles ────────────────────
    rows = bind.execute(sa.text(
        "SELECT u.id, u.role_id FROM za_users u WHERE u.role_id IS NOT NULL"
    )).fetchall()
    for user_id, role_id in rows:
        exists = bind.execute(sa.text(
            "SELECT 1 FROM za_user_roles WHERE user_id=:u AND role_id=:r"
        ), {"u": user_id, "r": role_id}).fetchone()
        if not exists:
            op.bulk_insert(user_roles_tbl, [{"user_id": user_id, "role_id": role_id}])


def downgrade() -> None:
    op.drop_table("za_user_roles")
