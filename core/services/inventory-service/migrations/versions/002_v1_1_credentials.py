"""v1.1: password strength, rotation policies, credential history, log backend config

Revision ID: 002
Revises: 001
Create Date: 2026-06-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # ── ALTER za_credentials ────────────────────────────────────────────────
    if "za_credentials" in existing_tables:
        existing_cols = {c["name"] for c in inspector.get_columns("za_credentials")}
        if "strength_score" not in existing_cols:
            op.add_column("za_credentials", sa.Column("strength_score", sa.Integer(), nullable=True))
        if "last_rotated_at" not in existing_cols:
            op.add_column("za_credentials", sa.Column("last_rotated_at", sa.DateTime(timezone=True), nullable=True))

    # ── za_rotation_policies ────────────────────────────────────────────────
    if "za_rotation_policies" not in existing_tables:
        op.create_table(
            "za_rotation_policies",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("credential_id", sa.String(36), sa.ForeignKey("za_credentials.id", ondelete="CASCADE"), nullable=False),
            sa.Column("rotation_days", sa.Integer(), nullable=False, server_default="90"),
            sa.Column("rotation_mode", sa.String(20), nullable=False, server_default="auto"),
            sa.Column("last_rotated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("next_rotation_due", sa.DateTime(timezone=True), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        )
        op.create_index("ix_za_rotation_policies_credential_id", "za_rotation_policies", ["credential_id"])

    # ── za_credential_history ───────────────────────────────────────────────
    if "za_credential_history" not in existing_tables:
        op.create_table(
            "za_credential_history",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("credential_id", sa.String(36), sa.ForeignKey("za_credentials.id", ondelete="CASCADE"), nullable=False),
            sa.Column("secret_ciphertext", sa.Text(), nullable=False),
            sa.Column("rotated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("rotated_by", sa.String(36), nullable=True),
        )
        op.create_index("ix_za_credential_history_cred_rotated",
                        "za_credential_history", ["credential_id", "rotated_at"])

    # ── za_log_backend_config ───────────────────────────────────────────────
    if "za_log_backend_config" not in existing_tables:
        op.create_table(
            "za_log_backend_config",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("backend", sa.String(20), nullable=False, server_default="local"),
            sa.Column("es_url", sa.Text(), nullable=False, server_default=""),
            sa.Column("es_api_key", sa.Text(), nullable=False, server_default=""),
            sa.Column("es_index_prefix", sa.String(100), nullable=False, server_default="seyalrun"),
            sa.Column("s3_bucket", sa.Text(), nullable=False, server_default=""),
            sa.Column("s3_region", sa.String(50), nullable=False, server_default=""),
            sa.Column("s3_access_key_id", sa.Text(), nullable=False, server_default=""),
            sa.Column("s3_secret_access_key", sa.Text(), nullable=False, server_default=""),
            sa.Column("s3_endpoint_url", sa.Text(), nullable=False, server_default=""),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table("za_log_backend_config")
    op.drop_index("ix_za_credential_history_cred_rotated", "za_credential_history")
    op.drop_table("za_credential_history")
    op.drop_index("ix_za_rotation_policies_credential_id", "za_rotation_policies")
    op.drop_table("za_rotation_policies")
    op.drop_column("za_credentials", "last_rotated_at")
    op.drop_column("za_credentials", "strength_score")
