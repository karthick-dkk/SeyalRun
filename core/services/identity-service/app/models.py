"""SQLAlchemy ORM models — identity DB. Mirrors schema/{postgres,mysql}/schema.sql."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _uid() -> str:
    return str(uuid.uuid4())


class ZARole(Base):
    __tablename__ = "za_roles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list["ZAUser"]] = relationship("ZAUser", back_populates="role", lazy="select")


class ZAUserGroup(Base):
    __tablename__ = "za_user_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    zabbix_usrgrpid: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Group-level policy doc (v1.2) — mirrors ZARole.permissions' JSON-doc convention so
    # new policies don't need a new migration each time. Shape:
    # {"mfa_enforced": bool, "setup_wizard": bool, "notifications_enabled": bool}.
    # Absent/false for every key = today's behavior (no group policy applied).
    policies: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members: Mapped[list["ZAUserGroupMember"]] = relationship("ZAUserGroupMember", back_populates="group", lazy="select")


class ZAUser(Base):
    __tablename__ = "za_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    zabbix_userid: Mapped[str | None] = mapped_column(String(50), nullable=True)
    role_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("za_roles.id", ondelete="SET NULL"), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_secret: Mapped[str] = mapped_column(Text, default="")  # base32 seed, vault-encrypted (v1.1)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # v1.1
    # Which second factor is active, if any — "totp" (authenticator app, uses totp_secret/
    # totp_enabled above) or "email" (OTP mailed on demand, no persistent secret needed).
    # None means MFA is off. Kept separate from totp_enabled so email-OTP doesn't need to
    # repurpose TOTP-specific columns.
    mfa_method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # First-login "account setup" wizard (v1.2) — set True once a user has been through
    # it (see api/auth.py's /auth/setup/complete). Existing users are backfilled to True
    # at migration time (they're not "new"); only accounts created after this ships and
    # who land in a setup_wizard-enabled group see it.
    setup_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Optional per-account login IP allowlist (v1.2) — list[str] of CIDR strings (e.g.
    # ["203.0.113.4/32"]). Validated with ipaddress.ip_network, same approach already
    # proven in api/internal.py::login_acls_check. Only enforced when ip_restriction_enabled
    # is also set (explicit toggle, v1.3 — previously implicit on "list non-empty", now
    # separated so an admin can flip enforcement off without losing the configured list).
    allowed_ips: Mapped[list] = mapped_column(JSON, default=list)
    ip_restriction_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # v1.3, opt-in
    # Single-session-per-user (v1.3) — opt-in per user; a group can also turn this on for
    # all its members (za_user_groups.policies.single_session_enabled), most-restrictive-
    # wins: enforced if EITHER this OR any of the user's groups has it on.
    single_session_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # PCI DSS Phase C — service account registry: a real object-type flag (not a
    # naming convention or role) so service accounts can be scoped/reviewed/
    # offboarded separately from human users. account_type is the human-readable
    # label; is_service_account is what code branches on (kept both — mirrors
    # mfa_method/totp_enabled's "flag + label" pairing above).
    is_service_account: Mapped[bool] = mapped_column(Boolean, default=False)
    account_type: Mapped[str] = mapped_column(String(20), default="human")  # human|service
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    role: Mapped[ZARole | None] = relationship("ZARole", back_populates="users")
    group_memberships: Mapped[list["ZAUserGroupMember"]] = relationship("ZAUserGroupMember", back_populates="user", lazy="select")


class ZAUserRole(Base):
    """Many-to-many user↔role assignment (v1.1 RBAC — a user can hold multiple roles)."""

    __tablename__ = "za_user_roles"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_roles.id", ondelete="CASCADE"), primary_key=True)


class ZAUserGroupMember(Base):
    __tablename__ = "za_user_group_members"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_users.id", ondelete="CASCADE"), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_user_groups.id", ondelete="CASCADE"), primary_key=True)

    user: Mapped[ZAUser] = relationship("ZAUser", back_populates="group_memberships")
    group: Mapped[ZAUserGroup] = relationship("ZAUserGroup", back_populates="members")


class ZAGroupRole(Base):
    """Group→role grant (v2): membership in the group inherits these roles.
    Effective roles = direct za_user_roles ∪ roles from every group the user is in."""

    __tablename__ = "za_group_roles"

    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_user_groups.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_roles.id", ondelete="CASCADE"), primary_key=True)


class ZAGroupNotifyConfig(Base):
    """Per-group email alert routing (v1.2) — mirrors ZAMailSettings' get-or-create-row
    pattern but keyed per group instead of singleton. On/off is controlled purely by the
    parent group's policies.notifications_enabled flag; this row just holds where/how
    much. Delivery reuses mailer.send_mail (SMTP/Graph) — no separate channel infra."""

    __tablename__ = "za_group_notify_config"

    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_user_groups.id", ondelete="CASCADE"), primary_key=True)
    emails: Mapped[list] = mapped_column(JSON, default=list)  # list[str]
    min_severity: Mapped[str] = mapped_column(String(20), default="medium")  # info|medium|critical
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ZAGroupIpRestriction(Base):
    """Per-group shared login-IP allowlist (v1.3) — same split as ZAGroupNotifyConfig:
    on/off lives on the parent group's policies.ip_restriction_enabled flag, this row
    just holds the CIDR values. Combines with any per-user allowlist via most-
    restrictive-wins (grouppolicy.py::ip_login_allowed) — every ACTIVE constraint
    (user-level and/or each enabled group's) must independently match the login IP."""

    __tablename__ = "za_group_ip_restrictions"

    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_user_groups.id", ondelete="CASCADE"), primary_key=True)
    cidrs: Mapped[list] = mapped_column(JSON, default=list)  # list[str]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ZASession(Base):
    __tablename__ = "za_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_users.id", ondelete="CASCADE"), nullable=False)
    jwt_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ZAAuthorization(Base):
    """PAM rule: (user OR user_group) -> (host OR host_group, inventory DB) -> credential -> actions."""

    __tablename__ = "za_authorizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Legacy single-value columns — kept populated (first array element) for back-compat.
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("za_users.id", ondelete="CASCADE"), nullable=True)
    user_group_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("za_user_groups.id", ondelete="CASCADE"), nullable=True)
    host_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    host_group_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # v1.1 many-to-many: one rule may grant multiple users/groups access to multiple hosts/groups.
    user_ids: Mapped[list] = mapped_column(JSON, default=list)
    user_group_ids: Mapped[list] = mapped_column(JSON, default=list)
    host_ids: Mapped[list] = mapped_column(JSON, default=list)
    host_group_ids: Mapped[list] = mapped_column(JSON, default=list)
    credential_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    credential_ids: Mapped[list] = mapped_column(JSON, default=list)
    actions: Mapped[list] = mapped_column(JSON, default=list)
    date_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    date_expired: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # PCI DSS Phase B — segregation of duties: a grant is inert (enabled forced False)
    # from creation/edit until a DIFFERENT admin/superadmin approves it (self-approval
    # blocked in the API layer). status mirrors ZAJobTemplate.requires_approval's
    # request/approve shape one model over. "enabled" stays the sole enforcement flag
    # every resolver already checks — approval just controls when it flips True.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending_approval")
    requested_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ZAAuthorizationApprovalToken(Base):
    """Single-use, per-recipient magic-link tokens for approving/rejecting a
    pending ZAAuthorization by email, without requiring the approver to log in
    first. Only the sha256 hash of the raw token is stored (same discipline as
    ZAApiToken) — the raw value exists only in the emailed URL and the moment
    it's presented back to GET /authorizations/email-action. One row per
    (authorization, eligible approver, action) — approver_user_id is fixed at
    send time, so a click can only ever record that specific admin/superadmin
    as the approver (never an anonymous "email" actor), preserving both the
    audit trail and the self-approval block."""

    __tablename__ = "za_authz_approval_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    authorization_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_authorizations.id", ondelete="CASCADE"), nullable=False)
    approver_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_users.id", ondelete="CASCADE"), nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)  # "approve" | "reject"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ZACommandGroup(Base):
    __tablename__ = "za_command_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    patterns: Mapped[list] = mapped_column(JSON, default=list)
    match_type: Mapped[str] = mapped_column(String(20), default="regex")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ZACommandFilter(Base):
    """SSH ACL: command group + scope -> allow|deny|confirm. Enforced by terminal-service (Phase 2)."""

    __tablename__ = "za_command_filters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    command_group_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_command_groups.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("za_users.id", ondelete="CASCADE"), nullable=True)
    user_group_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("za_user_groups.id", ondelete="CASCADE"), nullable=True)
    host_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    host_group_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(20), default="deny")
    priority: Mapped[int] = mapped_column(Integer, default=50)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ZALoginAcl(Base):
    """Time/IP/host login restriction. Enforced by api-gateway + terminal-service (Phase 2)."""

    __tablename__ = "za_login_acls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("za_users.id", ondelete="CASCADE"), nullable=True)
    user_group_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("za_user_groups.id", ondelete="CASCADE"), nullable=True)
    ip_cidr: Mapped[str | None] = mapped_column(String(64), nullable=True)
    time_start: Mapped[str | None] = mapped_column(String(5), nullable=True)
    time_end: Mapped[str | None] = mapped_column(String(5), nullable=True)
    days_of_week: Mapped[list] = mapped_column(JSON, default=lambda: [0, 1, 2, 3, 4, 5, 6])
    action: Mapped[str] = mapped_column(String(10), default="allow")
    priority: Mapped[int] = mapped_column(Integer, default=50)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ZAApiToken(Base):
    """Personal Access Token — Argon2id hash + API_TOKEN_PEPPER, raw token shown once."""

    __tablename__ = "za_api_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("za_users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    scopes: Mapped[list] = mapped_column(JSON, default=lambda: ["read"])
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ZAAuditLog(Base):
    __tablename__ = "za_audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    username: Mapped[str] = mapped_column(String(100), default="")
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), default="")
    resource_id: Mapped[str] = mapped_column(String(36), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Tamper-evident hash chain (T9): seq is a strict monotonic order assigned under an
    # advisory lock; entry_hash = H(seq, prev_hash, payload). Nullable so the migration
    # doesn't need to backfill; every new row populates all three.
    seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # PCI DSS Phase A structured-schema gap: PCI 10.2 wants user_id/event_type/
    # timestamp/source_ip/target_resource/result/session_id. All but the last two
    # already existed (username/action/created_at/ip_address/resource_type+id).
    # Nullable, not backfilled — each row's hash is fixed at write time, so old
    # rows simply carry these as None and the chain still verifies (see audit.py).
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    result: Mapped[str | None] = mapped_column(String(20), nullable=True)


class ZALoginAttempt(Base):
    """DB-backed brute-force lockout state, keyed by "username|ip".

    Survives restarts (unlike a pure in-memory counter) and needs no redis in
    identity-service. ``fail_count`` accumulates within the attempt window;
    ``locked_until`` is set once the threshold is crossed.
    """

    __tablename__ = "za_login_attempts"

    key: Mapped[str] = mapped_column(String(320), primary_key=True)
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ZAMailSettings(Base):
    """Singleton mail-delivery config for MFA email OTPs (mirrors ZALogBackendConfig's
    single-row pattern — services/inventory-service/app/models.py). Secrets
    (smtp_password, graph_client_secret) are vault-encrypted, never returned
    plaintext by the API (see api/mail_settings.py's masked-secret GET/PUT).
    """

    __tablename__ = "za_mail_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    provider: Mapped[str] = mapped_column(String(10), default="")  # "smtp" | "graph" | ""
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    from_address: Mapped[str] = mapped_column(String(320), default="")
    from_name: Mapped[str] = mapped_column(String(200), default="")

    smtp_host: Mapped[str] = mapped_column(String(255), default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=587)
    smtp_username: Mapped[str] = mapped_column(String(320), default="")
    smtp_password: Mapped[str] = mapped_column(Text, default="")  # vault-encrypted
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, default=True)

    graph_tenant_id: Mapped[str] = mapped_column(String(100), default="")
    graph_client_id: Mapped[str] = mapped_column(String(100), default="")
    graph_client_secret: Mapped[str] = mapped_column(Text, default="")  # vault-encrypted
    graph_sender_upn: Mapped[str] = mapped_column(String(320), default="")

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ZAAccessReviewCampaign(Base):
    """PCI DSS Phase C (7.2.4 periodic access review) — MVP: a labeled snapshot
    of every currently-active ZAAuthorization at creation time, walked to
    keep/revoke by an admin. Not a full GRC module — no role/group scope
    filtering, no multi-reviewer workflow — just enough to satisfy "reviewed
    on a schedule, with a record of who decided what."""

    __tablename__ = "za_access_review_campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")  # open|completed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["ZAAccessReviewItem"]] = relationship(
        "ZAAccessReviewItem", back_populates="campaign", lazy="select"
    )


class ZAAccessReviewItem(Base):
    __tablename__ = "za_access_review_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("za_access_review_campaigns.id", ondelete="CASCADE"), nullable=False
    )
    authorization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Snapshot at campaign-creation time, so the item still reads sensibly even if the
    # underlying authorization is later edited/revoked/deleted out from under it.
    authorization_name: Mapped[str] = mapped_column(String(200), default="")
    decision: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending|keep|revoke
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    campaign: Mapped[ZAAccessReviewCampaign] = relationship("ZAAccessReviewCampaign", back_populates="items")


class ZASetting(Base):
    """Generic key/value settings store (superadmin-editable via the UI).

    First key: "integration" -> {zabbix_console_url, zabbix_api_url, zabbix_api_token}.
    The gateway reads these DB-first and falls back to .env; the api token is never
    sent to the browser (see the /settings and /internal/settings endpoints).
    """

    __tablename__ = "za_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
