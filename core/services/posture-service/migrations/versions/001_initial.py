"""posture-service initial schema: host facts + immutable feed snapshots.

Revision ID: posture_001
Revises:
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "posture_001"
down_revision = None
branch_labels = None
depends_on = None

_BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _id() -> sa.Column:
    return sa.Column("id", _BIGID, primary_key=True, autoincrement=True)


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    if "posture_hosts" not in existing:
        op.create_table(
            "posture_hosts",
            sa.Column("host_id", sa.String(36), primary_key=True),
            sa.Column("hostname", sa.String(255), nullable=False, server_default=""),
            sa.Column("os_family", sa.String(16), nullable=False, server_default="linux"),
            sa.Column("distro", sa.String(128), nullable=False, server_default=""),
            sa.Column("distro_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("distro_family", sa.String(32), nullable=False, server_default=""),
            sa.Column("distro_version", sa.String(64), nullable=False, server_default=""),
            sa.Column("kernel", sa.String(128), nullable=False, server_default=""),
            sa.Column("arch", sa.String(32), nullable=False, server_default=""),
            sa.Column("pkg_manager", sa.String(16), nullable=False, server_default=""),
            sa.Column("packages_installed", sa.Integer, nullable=False, server_default="0"),
            sa.Column("pending_updates", sa.Integer, nullable=False, server_default="0"),
            sa.Column("security_updates", sa.Integer, nullable=False, server_default="0"),
            sa.Column("reboot_required", sa.Boolean, nullable=True),
            sa.Column("reboot_method", sa.String(64), nullable=False, server_default=""),
            sa.Column("warnings", sa.JSON, nullable=False),
            sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_result", sa.String(16), nullable=False, server_default=""),
            sa.Column("last_error", sa.Text, nullable=True),
            sa.Column("collect_state", sa.String(24), nullable=False, server_default="idle"),
            sa.Column("last_job_run_id", sa.String(36), nullable=True),
            sa.Column("last_requested_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_requested_by", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_posture_hosts_collect_state", "posture_hosts", ["collect_state"])

    if "posture_installed_packages" not in existing:
        op.create_table(
            "posture_installed_packages",
            _id(),
            sa.Column("host_id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("version", sa.String(255), nullable=False, server_default=""),
            sa.Column("arch", sa.String(32), nullable=False, server_default=""),
        )
        op.create_index("ix_posture_installed_host_name", "posture_installed_packages", ["host_id", "name"])

    if "posture_pending_updates" not in existing:
        op.create_table(
            "posture_pending_updates",
            _id(),
            sa.Column("host_id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("arch", sa.String(32), nullable=False, server_default=""),
            sa.Column("current", sa.String(255), nullable=False, server_default=""),
            sa.Column("available", sa.String(255), nullable=False, server_default=""),
            sa.Column("security", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("source", sa.String(128), nullable=False, server_default=""),
        )
        op.create_index("ix_posture_pending_host", "posture_pending_updates", ["host_id"])

    if "posture_feed_snapshots" not in existing:
        op.create_table(
            "posture_feed_snapshots",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("version", sa.String(64), nullable=False, unique=True),
            sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("imported_by", sa.String(36), nullable=True),
            sa.Column("imported_by_name", sa.String(200), nullable=False, server_default=""),
            sa.Column("signed_by_key", sa.String(200), nullable=False, server_default=""),
            sa.Column("bundle_sha256", sa.String(64), nullable=False, server_default=""),
            sa.Column("manifest", sa.JSON, nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="importing"),
            sa.Column("error", sa.Text, nullable=True),
            sa.Column("stats", sa.JSON, nullable=False),
            sa.Column("active", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("activated_by", sa.String(36), nullable=True),
        )

    if "posture_feed_osv" not in existing:
        op.create_table(
            "posture_feed_osv",
            _id(),
            sa.Column("snapshot_id", sa.String(36), nullable=False),
            sa.Column("vuln_id", sa.String(128), nullable=False),
            sa.Column("cve_id", sa.String(32), nullable=True),
            sa.Column("ecosystem", sa.String(64), nullable=False, server_default=""),
            sa.Column("package", sa.String(255), nullable=False, server_default=""),
            sa.Column("range_type", sa.String(16), nullable=False, server_default=""),
            sa.Column("introduced", sa.String(255), nullable=True),
            sa.Column("fixed", sa.String(255), nullable=True),
            sa.Column("last_affected", sa.String(255), nullable=True),
            sa.Column("severity", sa.String(32), nullable=True),
            sa.Column("modified", sa.String(40), nullable=True),
        )
        op.create_index("ix_posture_osv_snap_pkg", "posture_feed_osv", ["snapshot_id", "ecosystem", "package"])
        op.create_index("ix_posture_osv_snap_cve", "posture_feed_osv", ["snapshot_id", "cve_id"])

    if "posture_feed_epss" not in existing:
        op.create_table(
            "posture_feed_epss",
            _id(),
            sa.Column("snapshot_id", sa.String(36), nullable=False),
            sa.Column("cve_id", sa.String(32), nullable=False),
            sa.Column("score", sa.Float, nullable=False),
            sa.Column("percentile", sa.Float, nullable=False),
        )
        op.create_index("ix_posture_epss_snap_cve", "posture_feed_epss", ["snapshot_id", "cve_id"])

    if "posture_feed_kev" not in existing:
        op.create_table(
            "posture_feed_kev",
            _id(),
            sa.Column("snapshot_id", sa.String(36), nullable=False),
            sa.Column("cve_id", sa.String(32), nullable=False),
            sa.Column("date_added", sa.String(10), nullable=True),
            sa.Column("due_date", sa.String(10), nullable=True),
            sa.Column("ransomware", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("vendor", sa.String(255), nullable=False, server_default=""),
            sa.Column("product", sa.String(255), nullable=False, server_default=""),
        )
        op.create_index("ix_posture_kev_snap_cve", "posture_feed_kev", ["snapshot_id", "cve_id"])

    if "posture_feed_oval" not in existing:
        op.create_table(
            "posture_feed_oval",
            _id(),
            sa.Column("snapshot_id", sa.String(36), nullable=False),
            sa.Column("definition_id", sa.String(128), nullable=False),
            sa.Column("cve_id", sa.String(32), nullable=True),
            sa.Column("platform", sa.String(255), nullable=False, server_default=""),
            sa.Column("package", sa.String(255), nullable=False, server_default=""),
            sa.Column("fixed_version", sa.String(255), nullable=False, server_default=""),
            sa.Column("severity", sa.String(32), nullable=True),
        )
        op.create_index("ix_posture_oval_snap_pkg", "posture_feed_oval", ["snapshot_id", "package"])
        op.create_index("ix_posture_oval_snap_cve", "posture_feed_oval", ["snapshot_id", "cve_id"])


def downgrade() -> None:
    for table in ("posture_feed_oval", "posture_feed_kev", "posture_feed_epss", "posture_feed_osv",
                  "posture_feed_snapshots", "posture_pending_updates", "posture_installed_packages",
                  "posture_hosts"):
        op.drop_table(table)
