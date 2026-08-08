from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str
    password: str
    # Raw zbx_host/host_id from a Zabbix terminal deep-link, IF this login was forced
    # by the router guard specifically to reach it. A hint only — resolved and
    # validated server-side (see auth.py::_resolve_kiosk_host) before anything is
    # bound into the issued token; a bogus value just means no kiosk claim is minted.
    kiosk_target: str | None = None


class SSOInitRequest(BaseModel):
    username: str
    zabbix_user_type: int = 1


class SSOInitResponse(BaseModel):
    sso_code: str


class SSOExchangeRequest(BaseModel):
    sso_code: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: str
    email: str
    zabbix_userid: str | None = None
    role_id: str | None = None
    role_name: str | None = None
    roles: list[str] = []          # all role names (v1.1 multi-role)
    role_ids: list[str] = []       # all role ids (for the assignment UI)
    is_active: bool
    totp_enabled: bool = False
    mfa_method: str | None = None  # "totp" | "email" | None
    allowed_ips: list[str] = Field(default_factory=list)  # CIDR list; empty = unrestricted
    # PCI DSS Phase C — service account registry: a separate object TYPE from
    # human users (tagged, reviewable on schedule), not a role or a naming
    # convention. account_type is the human-readable label; is_service_account
    # is what code actually branches on.
    is_service_account: bool = False
    account_type: str = "human"  # "human" | "service"
    ip_restriction_enabled: bool = False  # explicit opt-in toggle, v1.3
    single_session_enabled: bool = False  # opt-in per user (or via group), v1.3
    must_change_password: bool = False
    created_at: datetime
    # Server-asserted for this login only (session-scoped, not a DB attribute of
    # the user) — a login forced through a Zabbix terminal deep-link. The token
    # is opaque now, so this must ride in the response body, not be decoded
    # client-side from a JWT claim.
    kiosk: bool = False
    kiosk_host_id: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    kiosk_target: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
    # Set when the minted session carries the mfa_pending claim — the token is
    # usable ONLY for POST /auth/mfa/verify-login (+ auth/nav) until that's
    # cleared (api-gateway enforces the allowlist; see main.py's mfa_pending block).
    mfa_required: bool = False
    mfa_method: str | None = None
    # Group-enforced: no mfa_method yet, but a group requires one — distinct from
    # mfa_required (which means "already enrolled, verify a code this session").
    mfa_setup_required: bool = False
    # Informational only (not gateway-enforced) — a group wants the first-login
    # setup wizard shown and this user hasn't been through it yet.
    needs_setup_wizard: bool = False


class UserCreate(BaseModel):
    username: str
    display_name: str = ""
    email: str = ""
    password: str
    role_id: str | None = None
    role_ids: list[str] = []       # v1.1 multi-role assignment
    is_service_account: bool = False


class UserUpdate(BaseModel):
    display_name: str | None = None
    email: str | None = None
    password: str | None = None
    role_id: str | None = None
    role_ids: list[str] | None = None   # v1.1 multi-role assignment
    is_active: bool | None = None
    allowed_ips: list[str] | None = None  # CIDR list; None = unchanged, [] = clear
    ip_restriction_enabled: bool | None = None
    single_session_enabled: bool | None = None
    is_service_account: bool | None = None


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    permissions: object = None
    is_builtin: bool = False


class RoleCreate(BaseModel):
    name: str
    description: str = ""
    permissions: dict = {}     # {"all": bool, "reveal": bool, "read": [...], "write": [...]}


class RoleUpdate(BaseModel):
    description: str | None = None
    permissions: dict | None = None


class UserGroupCreate(BaseModel):
    name: str
    description: str = ""


class UserGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    zabbix_usrgrpid: str | None = None
    policies: dict = Field(default_factory=dict)
    created_at: datetime


class GroupMembersUpdate(BaseModel):
    user_ids: list[str]


class GroupRolesUpdate(BaseModel):
    role_ids: list[str]


class GroupPoliciesUpdate(BaseModel):
    mfa_enforced: bool = False
    setup_wizard: bool = False
    notifications_enabled: bool = False
    single_session_enabled: bool = False
    ip_restriction_enabled: bool = False


class GroupNotifyConfigUpdate(BaseModel):
    emails: list[str] = Field(default_factory=list)
    min_severity: str = "medium"  # info | medium | critical


class GroupIpRestrictionUpdate(BaseModel):
    cidrs: list[str] = Field(default_factory=list)


class AuthorizationCreate(BaseModel):
    name: str
    # Legacy single-value fields (still accepted; folded into the arrays below).
    user_id: str | None = None
    user_group_id: str | None = None
    host_id: str | None = None
    host_group_id: str | None = None
    # v1.1 many-to-many: one rule → multiple users/groups → multiple hosts/groups.
    user_ids: list[str] = Field(default_factory=list)
    user_group_ids: list[str] = Field(default_factory=list)
    host_ids: list[str] = Field(default_factory=list)
    host_group_ids: list[str] = Field(default_factory=list)
    credential_id: str | None = None
    credential_ids: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=lambda: ["ssh"])
    date_start: datetime | None = None
    date_expired: datetime | None = None


class AuthorizationOut(AuthorizationCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    # Server-controlled — never accepted as client input (see AuthorizationCreate).
    # enabled in particular used to be a create/update input field; PCI DSS Phase B
    # made it exclusively approval-lifecycle-controlled (_require_approval/approve/
    # reject in api/authorizations.py). Moved here rather than left on
    # AuthorizationCreate, where _apply() silently ignored it — an old caller that
    # still sends `enabled` in a create/update body now gets the same (documented,
    # consistent-with-every-other-unknown-field) drop as any other unrecognized
    # field, rather than a field that looks meaningful but silently does nothing.
    enabled: bool = True
    status: str = "pending_approval"
    requested_by: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None


class CommandGroupCreate(BaseModel):
    name: str
    description: str = ""
    patterns: list[str] = Field(default_factory=list)
    match_type: str = "regex"


class CommandGroupOut(CommandGroupCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime


class CommandFilterCreate(BaseModel):
    name: str
    command_group_id: str
    user_id: str | None = None
    user_group_id: str | None = None
    host_id: str | None = None
    host_group_id: str | None = None
    action: str = "deny"  # allow|deny|confirm
    priority: int = 50
    enabled: bool = True


class CommandFilterOut(CommandFilterCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime


class LoginAclCreate(BaseModel):
    name: str
    user_id: str | None = None
    user_group_id: str | None = None
    ip_cidr: str | None = None
    time_start: str | None = None
    time_end: str | None = None
    days_of_week: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    action: str = "allow"  # allow|deny
    priority: int = 50
    enabled: bool = True


class LoginAclOut(LoginAclCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime


class ApiTokenCreate(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=lambda: ["read"])
    expires_at: datetime | None = None


class ApiTokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    token_prefix: str
    scopes: list[str]
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime
    revoked_at: datetime | None = None


class ApiTokenCreated(ApiTokenOut):
    token: str  # raw token, shown once


class TokenVerifyRequest(BaseModel):
    token: str


class TokenVerifyResponse(BaseModel):
    valid: bool
    user_id: str | None = None
    username: str | None = None
    role: str | None = None
    scopes: list[str] = Field(default_factory=list)


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str | None = None
    username: str
    action: str
    resource_type: str
    resource_id: str
    details: dict
    ip_address: str
    created_at: datetime


class AccessReviewCampaignCreate(BaseModel):
    name: str
    due_date: datetime | None = None


class AccessReviewItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    campaign_id: str
    authorization_id: str
    authorization_name: str
    decision: str
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


class AccessReviewCampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    due_date: datetime | None = None
    created_by: str | None = None
    status: str
    created_at: datetime
    completed_at: datetime | None = None
    total_items: int = 0
    pending_items: int = 0


class AccessReviewDecision(BaseModel):
    decision: str  # "keep" | "revoke"
