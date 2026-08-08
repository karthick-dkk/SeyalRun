"""PCI DSS Phase C (7.2.4): access-review MVP — za_access_review_campaigns +
za_access_review_items. A campaign snapshots every currently-active
ZAAuthorization at creation time; each item is walked to keep/revoke by an
admin, feeding a "revoke" decision directly into the authorization's existing
lifecycle (status="revoked", enabled=False) from Phase B rather than a
separate disable path.

Revision ID: 020
Revises: 019
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())

    if "za_access_review_campaigns" not in existing_tables:
        op.create_table(
            "za_access_review_campaigns",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by", sa.String(36), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="open"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "za_access_review_items" not in existing_tables:
        op.create_table(
            "za_access_review_items",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "campaign_id", sa.String(36),
                sa.ForeignKey("za_access_review_campaigns.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("authorization_id", sa.String(36), nullable=False),
            sa.Column("authorization_name", sa.String(200), nullable=False, server_default=""),
            sa.Column("decision", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("reviewed_by", sa.String(36), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_za_access_review_items_campaign_id", "za_access_review_items", ["campaign_id"])

    # Seed the "access-reviews" segment onto the stored admin role permission doc —
    # libs.rbaccore.BUILTIN_ROLE_PERMS is only the identity-down FALLBACK; the
    # DB-stored ZARole.permissions doc (seeded once, then live-editable) is what
    # api-gateway actually enforces in normal operation. Same update-in-place style
    # as migrations 013/014.
    roles = sa.table("za_roles", sa.column("name", sa.String), sa.column("permissions", sa.JSON))
    ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
    for name, doc in bind.execute(sa.select(roles.c.name, roles.c.permissions)).fetchall():
        if name != "admin":
            continue
        new_doc = dict(doc or {})
        perms = dict(new_doc.get("perms") or {})
        if "access-reviews" not in perms:
            perms["access-reviews"] = ALL_METHODS
            new_doc["perms"] = perms
            op.execute(roles.update().where(roles.c.name == name).values(permissions=new_doc))


def downgrade() -> None:
    op.drop_table("za_access_review_items")
    op.drop_table("za_access_review_campaigns")
