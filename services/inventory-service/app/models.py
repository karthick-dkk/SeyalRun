"""SQLAlchemy ORM models — inventory DB. Mirrors schema/{postgres,mysql}/schema.sql."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _uid() -> str:
    return str(uuid.uuid4())


class ZAZone(Base):
    __tablename__ = "za_zones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    # Zone nesting — the ProxyJump chain for a host in this zone is this zone's own
    # gateway plus every ancestor's gateway, root-first. SET NULL (not CASCADE) so
    # deleting a parent zone detaches its children rather than deleting them.
    parent_zone_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("za_zones.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    gateways: Mapped[list["ZAGateway"]] = relationship("ZAGateway", back_populates="zone", lazy="select")
    hosts: Mapped[list["ZAHost"]] = relationship("ZAHost", back_populates="zone", lazy="select")


class ZAGateway(Base):
    __tablename__ = "za_gateways"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    zone_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("za_zones.id", ondelete="CASCADE"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=22)
    username: Mapped[str] = mapped_column(String(100), default="")
    credential_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    zone: Mapped[ZAZone | None] = relationship("ZAZone", back_populates="gateways")


class ZAHostGroup(Base):
    __tablename__ = "za_host_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members: Mapped[list["ZAHostGroupMember"]] = relationship("ZAHostGroupMember", back_populates="group", lazy="select")


class ZAHost(Base):
    __tablename__ = "za_hosts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    zabbix_hostid: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    ip: Mapped[str] = mapped_column(String(100), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=22)
    os_type: Mapped[str] = mapped_column(String(20), default="linux")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    zone_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("za_zones.id", ondelete="SET NULL"), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_reachable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_ping_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # PCI DSS Phase D — production-host gating: automation-service forces any job
    # run targeting a production host through the approval flow (Phase B's
    # ZAJobTemplate.requires_approval path), regardless of the template's own
    # setting. See services/automation-service/app/api/job_templates.py::run_template.
    is_production: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    zone: Mapped[ZAZone | None] = relationship("ZAZone", back_populates="hosts")
    group_memberships: Mapped[list["ZAHostGroupMember"]] = relationship("ZAHostGroupMember", back_populates="host", lazy="select")
    credentials: Mapped[list["ZACredential"]] = relationship(
        "ZACredential", secondary="za_credential_host_links", back_populates="hosts", lazy="select"
    )


class ZAHostGroupMember(Base):
    __tablename__ = "za_host_group_members"

    host_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_hosts.id", ondelete="CASCADE"), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_host_groups.id", ondelete="CASCADE"), primary_key=True)

    host: Mapped[ZAHost] = relationship("ZAHost", back_populates="group_memberships")
    group: Mapped[ZAHostGroup] = relationship("ZAHostGroup", back_populates="members")


class ZACredentialTemplate(Base):
    """Account Template — defaults applied when creating a new credential."""

    __tablename__ = "za_credential_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    secret_type: Mapped[str] = mapped_column(String(20), default="password")
    description: Mapped[str] = mapped_column(Text, default="")
    default_username: Mapped[str] = mapped_column(String(100), default="")
    default_params: Mapped[dict] = mapped_column(JSON, default=dict)
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    rotation_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ZACredential(Base):
    """Encrypted credential — secret_ciphertext is AES-256-GCM (libs/dbcore.crypto)."""

    __tablename__ = "za_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(200), default="")
    template_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("za_credential_templates.id", ondelete="SET NULL"), nullable=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    secret_type: Mapped[str] = mapped_column(String(20), default="password")  # password|ssh_key|vault_path
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    # PCI DSS Phase C key hierarchy: the per-row DEK that encrypted secret_ciphertext,
    # wrapped by the active KeyProvider (see app/vault.py). NULL on rows written before
    # this shipped — decrypt() falls back to the old single-KEK scheme for those; every
    # new write (create/update/rotate) populates it going forward.
    wrapped_dek: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_scope: Mapped[str] = mapped_column(String(20), default="host")  # host|template
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_sudo: Mapped[bool] = mapped_column(Boolean, default=False)  # privilege escalation for account ops
    is_push_account: Mapped[bool] = mapped_column(Boolean, default=False)  # used to push/manage accounts on hosts
    strength_score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # zxcvbn 0-4 (v1.1)
    last_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # v1.1
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    hosts: Mapped[list[ZAHost]] = relationship(
        "ZAHost", secondary="za_credential_host_links", back_populates="credentials", lazy="select"
    )


class ZARotationPolicy(Base):
    """Per-credential password/secret rotation policy (v1.1)."""

    __tablename__ = "za_rotation_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    credential_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_credentials.id", ondelete="CASCADE"), nullable=False)
    rotation_days: Mapped[int] = mapped_column(Integer, default=90)
    rotation_mode: Mapped[str] = mapped_column(String(20), default="auto")  # auto|manual
    last_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_rotation_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ZACredentialHistory(Base):
    """Append-only archive of prior secret_ciphertext on rotation (v1.1)."""

    __tablename__ = "za_credential_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    credential_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_credentials.id", ondelete="CASCADE"), nullable=False)
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    # Must be archived alongside secret_ciphertext whenever the row it's copied
    # from used envelope encryption (ZACredential.wrapped_dek set) — otherwise
    # the live row's wrapped_dek gets overwritten on the same rotation that
    # creates this history row, and the archived ciphertext becomes permanently
    # undecryptable (no DEK anywhere can unwrap it again). Null for rows copied
    # from a pre-Phase-C credential still on the legacy single-KEK scheme.
    wrapped_dek: Mapped[str | None] = mapped_column(Text, nullable=True)
    rotated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rotated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class ZALogBackendConfig(Base):
    """Single-row admin config for the centralized log backend (v1.1, Feature 1).
    es_api_key / s3_secret_access_key are stored vault-encrypted."""

    __tablename__ = "za_log_backend_config"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    backend: Mapped[str] = mapped_column(String(20), default="local")  # local|elasticsearch|s3|es+s3
    es_url: Mapped[str] = mapped_column(Text, default="")
    es_api_key: Mapped[str] = mapped_column(Text, default="")
    es_index_prefix: Mapped[str] = mapped_column(String(100), default="seyalrun")
    es_verify_ssl: Mapped[bool] = mapped_column(Boolean, default=True)  # uncheck for self-signed ES
    s3_bucket: Mapped[str] = mapped_column(Text, default="")
    s3_region: Mapped[str] = mapped_column(String(50), default="")
    s3_access_key_id: Mapped[str] = mapped_column(Text, default="")
    s3_secret_access_key: Mapped[str] = mapped_column(Text, default="")
    s3_endpoint_url: Mapped[str] = mapped_column(Text, default="")
    # Content routing: log category -> list of backends ("local"|"elasticsearch"|"s3").
    # Categories: app, command, audit, recording. Admin-managed in the Log Backend UI.
    routing: Mapped[dict] = mapped_column(JSON, default=dict)
    # PCI DSS Phase B (10.5.1): per-category retention, admin-editable alongside
    # routing instead of a hardcoded .env global. Shape: {"audit_days": 180}.
    # Extensible per category without a new migration each time (mirrors routing's
    # own JSON-doc convention). Currently only "audit_days" is read (housekeeping.py's
    # audit_log_archive job); absent/empty falls back to identity-service's own
    # AUDIT_LOG_RETENTION_DAYS .env default.
    retention: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ZACredentialHostLink(Base):
    __tablename__ = "za_credential_host_links"

    credential_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_credentials.id", ondelete="CASCADE"), primary_key=True)
    host_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_hosts.id", ondelete="CASCADE"), primary_key=True)
