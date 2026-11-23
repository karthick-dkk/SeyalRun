"""posture-service schema (DB seyalrun_posture).

Generic SQLAlchemy types only, so the same schema runs on PostgreSQL and MySQL
(libs/dbcore). host_id is inventory-service's host id — posture never invents
its own host identity, it annotates inventory's.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from libs.dbcore import Base
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

# BIGINT on Postgres/MySQL, INTEGER on SQLite — autoincrement works on all three.
_BigId = BigInteger().with_variant(Integer(), "sqlite")


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


class PostureHost(Base):
    __tablename__ = "posture_hosts"

    host_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # linux | windows. Carried from day one so a Windows collector is new code,
    # not a migration.
    os_family: Mapped[str] = mapped_column(String(16), nullable=False, default="linux")
    distro: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    distro_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    distro_family: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    distro_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    kernel: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    arch: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    pkg_manager: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    packages_installed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_updates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    security_updates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reboot_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reboot_method: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    warnings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Outcome of the most recent attempt: "" (none yet) | ok | failed.
    last_result: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # idle | queued | running | awaiting_approval — the in-flight facts run, if any.
    collect_state: Mapped[str] = mapped_column(String(24), nullable=False, default="idle")
    last_job_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_requested_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class PostureInstalledPackage(Base):
    __tablename__ = "posture_installed_packages"
    __table_args__ = (Index("ix_posture_installed_host_name", "host_id", "name"),)

    id: Mapped[int] = mapped_column(_BigId, primary_key=True, autoincrement=True)
    host_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    arch: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    # Debian family: the source package and its version (advisories name
    # source packages). Empty on RPM hosts and on facts from before M2.
    source: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    source_version: Mapped[str] = mapped_column(String(255), nullable=False, default="")


class PosturePendingUpdate(Base):
    __tablename__ = "posture_pending_updates"
    __table_args__ = (Index("ix_posture_pending_host", "host_id"),)

    id: Mapped[int] = mapped_column(_BigId, primary_key=True, autoincrement=True)
    host_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    arch: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    current: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    available: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    security: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False, default="")


class FeedSnapshot(Base):
    """One imported, signature-verified feed bundle. Immutable once ready:
    nothing updates its rows; a newer bundle is a new snapshot."""

    __tablename__ = "posture_feed_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    version: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    imported_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    imported_by_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    signed_by_key: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    bundle_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # importing | ready | failed. Only a ready snapshot can be activated.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="importing")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # {"osv": {"entries": n, "rows": m}, ...}
    stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class FeedOsv(Base):
    __tablename__ = "posture_feed_osv"
    __table_args__ = (
        Index("ix_posture_osv_snap_pkg", "snapshot_id", "ecosystem", "package"),
        Index("ix_posture_osv_snap_cve", "snapshot_id", "cve_id"),
    )

    id: Mapped[int] = mapped_column(_BigId, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    vuln_id: Mapped[str] = mapped_column(String(128), nullable=False)
    cve_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ecosystem: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    package: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    range_type: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    introduced: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fixed: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_affected: Mapped[str | None] = mapped_column(String(255), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    modified: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cvss: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str | None] = mapped_column(String(255), nullable=True)


class FeedEpss(Base):
    __tablename__ = "posture_feed_epss"
    __table_args__ = (Index("ix_posture_epss_snap_cve", "snapshot_id", "cve_id"),)

    id: Mapped[int] = mapped_column(_BigId, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    cve_id: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    percentile: Mapped[float] = mapped_column(Float, nullable=False)


class FeedKev(Base):
    __tablename__ = "posture_feed_kev"
    __table_args__ = (Index("ix_posture_kev_snap_cve", "snapshot_id", "cve_id"),)

    id: Mapped[int] = mapped_column(_BigId, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    cve_id: Mapped[str] = mapped_column(String(32), nullable=False)
    date_added: Mapped[str | None] = mapped_column(String(10), nullable=True)
    due_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ransomware: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    vendor: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    product: Mapped[str] = mapped_column(String(255), nullable=False, default="")


class FeedOval(Base):
    __tablename__ = "posture_feed_oval"
    __table_args__ = (
        Index("ix_posture_oval_snap_pkg", "snapshot_id", "package"),
        Index("ix_posture_oval_snap_cve", "snapshot_id", "cve_id"),
    )

    id: Mapped[int] = mapped_column(_BigId, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    definition_id: Mapped[str] = mapped_column(String(128), nullable=False)
    cve_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    platform: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    package: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    fixed_version: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    severity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cvss: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str | None] = mapped_column(String(255), nullable=True)


# ── M2: findings ─────────────────────────────────────────────────────────────

class PostureFinding(Base):
    """One vulnerability on one host. Dedupe key (host_id, cve_id, package)."""

    __tablename__ = "posture_findings"
    __table_args__ = (
        UniqueConstraint("host_id", "cve_id", "package", name="uq_posture_finding_key"),
        Index("ix_posture_findings_status_risk", "status", "risk_score"),
        Index("ix_posture_findings_host_status", "host_id", "status"),
        Index("ix_posture_findings_cve", "cve_id"),
        Index("ix_posture_findings_last_seen", "last_seen"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    host_id: Mapped[str] = mapped_column(String(36), nullable=False)
    cve_id: Mapped[str] = mapped_column(String(64), nullable=False)
    package: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    installed_version: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    fixed_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    cvss: Mapped[float | None] = mapped_column(Float, nullable=True)
    epss: Mapped[float | None] = mapped_column(Float, nullable=True)
    epss_percentile: Mapped[float | None] = mapped_column(Float, nullable=True)
    kev: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sources: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    references: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # open | fixed | accepted
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    fixed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class PostureFindingEvent(Base):
    """A finding's history: detected, reported by another scanner, fixed,
    reopened, status changes. Written on changes only, never per sighting."""

    __tablename__ = "posture_finding_events"
    __table_args__ = (Index("ix_posture_finding_events_finding", "finding_id", "at"),)

    id: Mapped[int] = mapped_column(_BigId, primary_key=True, autoincrement=True)
    finding_id: Mapped[str] = mapped_column(String(36), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    by: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class PostureScan(Base):
    """One scanner run: a native match of one host, or one imported file."""

    __tablename__ = "posture_scans"
    __table_args__ = (Index("ix_posture_scans_scanner_started", "scanner", "started_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    scanner: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="import")  # import | native
    host_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # running | ok | failed | skipped
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    findings_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    findings_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    findings_fixed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unmatched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requested_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    requested_by_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")


class PostureUnmatched(Base):
    """Results for a host posture could not correlate to inventory. Kept until
    an admin assigns them to a host; never dropped."""

    __tablename__ = "posture_unmatched"
    __table_args__ = (Index("ix_posture_unmatched_status_received", "status", "received_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    scan_id: Mapped[str] = mapped_column(String(36), nullable=False)
    scanner: Mapped[str] = mapped_column(String(64), nullable=False)
    reported_host: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    reported_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reported_ref: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    cve_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # pending | assigned
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    assigned_host_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class PostureUnmatchedItem(Base):
    __tablename__ = "posture_unmatched_items"
    __table_args__ = (Index("ix_posture_unmatched_items_parent", "unmatched_id"),)

    id: Mapped[int] = mapped_column(_BigId, primary_key=True, autoincrement=True)
    unmatched_id: Mapped[str] = mapped_column(String(36), nullable=False)
    cve_id: Mapped[str] = mapped_column(String(64), nullable=False)
    package: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    installed_version: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    fixed_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    cvss: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    references: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
