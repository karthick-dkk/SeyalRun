"""MFA login gate + O365/SMTP mail delivery: za_users.mfa_method, za_mail_settings
table, and seeds the "mfa" role flag onto superadmin/admin's permission docs.

Revision ID: 014
Revises: 013
Create Date: 2026-07-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    cols = {c["name"] for c in inspector.get_columns("za_users")}
    if "mfa_method" not in cols:
        op.add_column("za_users", sa.Column("mfa_method", sa.String(10), nullable=True))

    if "za_mail_settings" not in set(inspector.get_table_names()):
        op.create_table(
            "za_mail_settings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("provider", sa.String(10), nullable=False, server_default=""),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("from_address", sa.String(320), nullable=False, server_default=""),
            sa.Column("from_name", sa.String(200), nullable=False, server_default=""),
            sa.Column("smtp_host", sa.String(255), nullable=False, server_default=""),
            sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="587"),
            sa.Column("smtp_username", sa.String(320), nullable=False, server_default=""),
            sa.Column("smtp_password", sa.Text(), nullable=False, server_default=""),
            sa.Column("smtp_use_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("graph_tenant_id", sa.String(100), nullable=False, server_default=""),
            sa.Column("graph_client_id", sa.String(100), nullable=False, server_default=""),
            sa.Column("graph_client_secret", sa.Text(), nullable=False, server_default=""),
            sa.Column("graph_sender_upn", sa.String(320), nullable=False, server_default=""),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    # Seed the "mfa" capability flag onto superadmin/admin's stored permission docs
    # (superadmin's "all": True already bypasses this, but it gets the flag too for
    # consistency with BUILTIN_ROLE_PERMS in libs/rbaccore) — same update-in-place
    # style as migration 013.
    roles = sa.table("za_roles", sa.column("name", sa.String), sa.column("permissions", sa.JSON))
    for name, doc in bind.execute(sa.select(roles.c.name, roles.c.permissions)).fetchall():
        flags = list((doc or {}).get("flags") or [])
        if name in ("superadmin", "admin") and "mfa" not in flags:
            flags.append("mfa")
            new_doc = dict(doc or {})
            new_doc["flags"] = flags
            op.execute(roles.update().where(roles.c.name == name).values(permissions=new_doc))


def downgrade() -> None:
    op.drop_table("za_mail_settings")
    op.drop_column("za_users", "mfa_method")
