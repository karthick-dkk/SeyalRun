"""posture-service M2: findings, finding history, scans, unmatched queue; source
package on installed packages; CVSS + summary on feed advisory rows.

Revision ID: posture_002
Revises: posture_001
Create Date: 2026-11-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "posture_002"
down_revision = "posture_001"
branch_labels = None
depends_on = None

_BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    if "posture_installed_packages" in existing:
        have = _cols("posture_installed_packages")
        if "source" not in have:
            op.add_column("posture_installed_packages",
                          sa.Column("source", sa.String(255), nullable=False, server_default=""))
        if "source_version" not in have:
            op.add_column("posture_installed_packages",
                          sa.Column("source_version", sa.String(255), nullable=False, server_default=""))
    for table in ("posture_feed_osv", "posture_feed_oval"):
        if table in existing:
            have = _cols(table)
            if "cvss" not in have:
                op.add_column(table, sa.Column("cvss", sa.Float, nullable=True))
            if "summary" not in have:
                op.add_column(table, sa.Column("summary", sa.String(255), nullable=True))

    if "posture_findings" not in existing:
        op.create_table(
            "posture_findings",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("host_id", sa.String(36), nullable=False),
            sa.Column("cve_id", sa.String(64), nullable=False),
            sa.Column("package", sa.String(255), nullable=False, server_default=""),
            sa.Column("installed_version", sa.String(255), nullable=False, server_default=""),
            sa.Column("fixed_version", sa.String(255), nullable=True),
            sa.Column("severity", sa.String(16), nullable=False, server_default="unknown"),
            sa.Column("cvss", sa.Float, nullable=True),
            sa.Column("epss", sa.Float, nullable=True),
            sa.Column("epss_percentile", sa.Float, nullable=True),
            sa.Column("kev", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("risk_score", sa.Integer, nullable=False, server_default="0"),
            sa.Column("sources", sa.JSON, nullable=False),
            sa.Column("summary", sa.Text, nullable=True),
            sa.Column("references", sa.JSON, nullable=False),
            sa.Column("first_seen", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("status", sa.String(16), nullable=False, server_default="open"),
            sa.Column("status_reason", sa.Text, nullable=True),
            sa.Column("fixed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("host_id", "cve_id", "package", name="uq_posture_finding_key"),
        )
        op.create_index("ix_posture_findings_status_risk", "posture_findings", ["status", "risk_score"])
        op.create_index("ix_posture_findings_host_status", "posture_findings", ["host_id", "status"])
        op.create_index("ix_posture_findings_cve", "posture_findings", ["cve_id"])
        op.create_index("ix_posture_findings_last_seen", "posture_findings", ["last_seen"])

    if "posture_finding_events" not in existing:
        op.create_table(
            "posture_finding_events",
            sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
            sa.Column("finding_id", sa.String(36), nullable=False),
            sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("event", sa.String(64), nullable=False),
            sa.Column("by", sa.String(200), nullable=False, server_default=""),
            sa.Column("details", sa.JSON, nullable=False),
        )
        op.create_index("ix_posture_finding_events_finding", "posture_finding_events", ["finding_id", "at"])

    if "posture_scans" not in existing:
        op.create_table(
            "posture_scans",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("scanner", sa.String(64), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False, server_default="import"),
            sa.Column("host_id", sa.String(36), nullable=True),
            sa.Column("filename", sa.String(255), nullable=False, server_default=""),
            sa.Column("sha256", sa.String(64), nullable=False, server_default=""),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="running"),
            sa.Column("error", sa.Text, nullable=True),
            sa.Column("findings_created", sa.Integer, nullable=False, server_default="0"),
            sa.Column("findings_updated", sa.Integer, nullable=False, server_default="0"),
            sa.Column("findings_fixed", sa.Integer, nullable=False, server_default="0"),
            sa.Column("unmatched", sa.Integer, nullable=False, server_default="0"),
            sa.Column("requested_by", sa.String(36), nullable=True),
            sa.Column("requested_by_name", sa.String(200), nullable=False, server_default=""),
        )
        op.create_index("ix_posture_scans_scanner_started", "posture_scans", ["scanner", "started_at"])

    if "posture_unmatched" not in existing:
        op.create_table(
            "posture_unmatched",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("scan_id", sa.String(36), nullable=False),
            sa.Column("scanner", sa.String(64), nullable=False),
            sa.Column("reported_host", sa.String(255), nullable=False, server_default=""),
            sa.Column("reported_ip", sa.String(64), nullable=True),
            sa.Column("reported_ref", sa.JSON, nullable=False),
            sa.Column("cve_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("assigned_host_id", sa.String(36), nullable=True),
            sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("assigned_by", sa.String(36), nullable=True),
        )
        op.create_index("ix_posture_unmatched_status_received", "posture_unmatched", ["status", "received_at"])

    if "posture_unmatched_items" not in existing:
        op.create_table(
            "posture_unmatched_items",
            sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
            sa.Column("unmatched_id", sa.String(36), nullable=False),
            sa.Column("cve_id", sa.String(64), nullable=False),
            sa.Column("package", sa.String(255), nullable=False, server_default=""),
            sa.Column("installed_version", sa.String(255), nullable=False, server_default=""),
            sa.Column("fixed_version", sa.String(255), nullable=True),
            sa.Column("severity", sa.String(32), nullable=False, server_default=""),
            sa.Column("cvss", sa.Float, nullable=True),
            sa.Column("summary", sa.Text, nullable=True),
            sa.Column("references", sa.JSON, nullable=False),
        )
        op.create_index("ix_posture_unmatched_items_parent", "posture_unmatched_items", ["unmatched_id"])


def downgrade() -> None:
    for table in ("posture_unmatched_items", "posture_unmatched", "posture_scans",
                  "posture_finding_events", "posture_findings"):
        op.drop_table(table)
    for table in ("posture_feed_oval", "posture_feed_osv"):
        op.drop_column(table, "summary")
        op.drop_column(table, "cvss")
    op.drop_column("posture_installed_packages", "source_version")
    op.drop_column("posture_installed_packages", "source")
