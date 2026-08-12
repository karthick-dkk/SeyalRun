"""Gateway RBAC — zero-trust, default-deny, data-driven (v2: capability roles).

Authorization is driven by each role's stored permission document (fetched from
identity-service and cached here, refreshed periodically), so admins can create custom
roles. Built-in role docs come from libs.rbaccore.BUILTIN_ROLE_PERMS — the ONE
definition, shared with the identity-service seed — used as the identity-down fallback
so built-in access never breaks while the cache is empty.

Roles grant CAPABILITY (feature/verb). Resource access (which host, which credential) is
a SEPARATE mandatory gate in za_authorizations — see rbaccore.bypasses_authz.
Permission document shape: see libs/rbaccore.
"""

from __future__ import annotations

import logging

from libs.rbaccore import (
    BUILTIN_ROLE_PERMS,
    bypasses_authz,
    doc_can_mfa,
    doc_can_reveal,
    merge_roles,
    primary_role as _primary_role,
    role_allows,
)

logger = logging.getLogger(__name__)

# segments every authenticated caller may use (login/mfa/self-service) — notifications
# are inherently per-caller (own inbox/preferences only, enforced by X-User-Id scoping
# in automation-service, not a role capability), so every role gets them like auth.
_ALWAYS = {"auth", "notifications"}

# ── Live cache: role name -> permission document (populated from identity) ────
_ROLE_PERMS: dict[str, dict] = {}


def set_role_perms(perms: dict[str, dict]) -> None:
    global _ROLE_PERMS
    if perms:
        _ROLE_PERMS = perms


def primary_role(roles: list[str]) -> str:
    return _primary_role(roles)


def _doc_for(role: str) -> dict | None:
    """Live cache first; fall back to the shared built-in doc while the cache is
    cold (identity-down). Unknown custom roles with no cached doc => deny."""
    doc = _ROLE_PERMS.get(role)
    if doc is not None:
        return doc
    return BUILTIN_ROLE_PERMS.get(role)


def _role_allows(role: str, method: str, seg: str) -> bool:
    return role_allows(_doc_for(role), method, seg)


def can_reveal(roles: list[str]) -> bool:
    return any(doc_can_reveal(_doc_for(r)) for r in roles)


def can_use_mfa(roles: list[str]) -> bool:
    return any(doc_can_mfa(_doc_for(r)) for r in roles)


def is_authorized(roles: list[str], method: str, path: str) -> bool:
    seg = path.split("/", 1)[0]
    # Enrolling in MFA needs the "mfa" capability flag even though the rest of
    # auth/* (login, mfa/verify-login, change-password, ...) is unconditionally
    # reachable below via _ALWAYS — this check must run BEFORE that shortcut.
    if method == "POST" and path in ("auth/mfa/setup", "auth/mfa/setup-email"):
        return can_use_mfa(roles)
    if seg in _ALWAYS:
        return True
    # Revealing a stored credential secret needs an explicit reveal capability.
    if method == "GET" and seg == "credentials" and path.endswith("/reveal"):
        return can_reveal(roles)
    # Running an existing playbook and creating/editing a template are both
    # POST on the same `job-templates` segment — separate them so a role can be
    # granted "run" without also getting "create" (see libs.rbaccore docstring).
    if method == "POST" and seg == "job-templates" and path.endswith("/run"):
        return (any(_role_allows(r, "POST", "job-templates") for r in roles)
                or any(_role_allows(r, "POST", "playbook-run") for r in roles))
    return any(_role_allows(r, method, seg) for r in roles)


# Nav area → (method, upstream segment) it needs. Used to tell the frontend which pages
# the caller may actually open, so it can hide the rest (authoritative — same is_authorized).
_NAV_SEGMENTS: dict[str, tuple[str, str]] = {
    "dashboard": ("GET", "metrics"),
    "hosts": ("GET", "hosts"),
    "assets": ("GET", "assets"),
    "zones": ("GET", "zones"),
    "sessions": ("GET", "ssh"),
    "terminal": ("GET", "ssh"),
    "recordings": ("GET", "recordings"),
    "jobs": ("GET", "job-runs"),
    "admin.users": ("GET", "users"),
    "admin.roles": ("GET", "roles"),
    "admin.authorizations": ("GET", "authorizations"),
    "admin.credentials": ("GET", "credentials"),
    "admin.zones": ("GET", "zones"),
    "admin.security": ("GET", "command-filters"),
    # POST (not GET) — "user" role legitimately has job-templates:GET (browse to run,
    # via the top-level "automation" area below) but must NOT see the manage-templates
    # admin page. Only roles that can actually create/edit templates should.
    "admin.automation": ("POST", "job-templates"),
    "admin.zabbix-integration": ("GET", "trigger-bindings"),
    "admin.integration": ("GET", "settings"),
    "admin.platform": ("GET", "settings"),
    "admin.health": ("GET", "metrics"),
    "admin.housekeeping": ("GET", "housekeeping"),
    "admin.log-backend": ("GET", "log-backend"),
    "admin.mail-settings": ("GET", "settings"),
    # Not an admin page — read by SecurityView.vue (self-service, any authenticated
    # user) to decide whether to offer MFA enrollment. Routes through the same
    # can_use_mfa() special-case in is_authorized() as the real POST /auth/mfa/setup.
    "security.mfa": ("POST", "auth/mfa/setup"),
    "admin.audit": ("GET", "audit"),
    "automation": ("GET", "job-templates"),
}


def nav_permissions(roles: list[str]) -> dict[str, bool]:
    """Which nav areas the caller may open — computed from the live RBAC matrix.
    'assets' is the host-management page; it also needs host read to function, so it only
    shows when both the 'assets' grant and 'hosts' read are present."""
    perms = {area: is_authorized(roles, method, seg) for area, (method, seg) in _NAV_SEGMENTS.items()}
    # Assets needs host read to function; it shows for roles with an explicit 'assets' grant
    # OR host write (managing hosts), so admins keep it without an extra grant.
    perms["assets"] = perms["hosts"] and (perms["assets"] or is_authorized(roles, "POST", "hosts"))
    return perms


# VISIBILITY (host list), NOT the access gate. Built-in broad roles see the full
# host list; custom roles are filtered to their authorized hosts (default-deny
# visibility). The mandatory ACCESS gate (connect/reveal/run) is separate and uses
# rbaccore.bypasses_authz ({superadmin, admin} only).
_SEE_ALL_HOSTS = frozenset({"superadmin", "admin", "support"})


def sees_all_hosts(roles: list[str]) -> bool:
    return any(r in _SEE_ALL_HOSTS for r in (roles or []))


def downstream_role(roles: list[str], method: str, seg: str) -> str:
    """Role forwarded to upstream services.

    - superadmin/admin pass through as-is.
    - terminal (ssh) NEVER elevates — terminal-service bypasses PAM for admins, so a
      guest/custom role must keep its real role for correct host scoping.
    - authorized writes vouch 'admin' (downstream write-guards are admin-gated CRUD, which
      the gateway has already authorized — this is what lets custom roles write).
    - GETs keep the real role so downstream per-role read scoping (e.g. recordings,
      which stays PAM-authorization-scoped for support/user — only their write
      capability over inventory/credential/playbook records is broad) stays correct.
    """
    if "superadmin" in roles:
        return "superadmin"
    if "admin" in roles:
        return "admin"
    if seg == "ssh":
        return primary_role(roles)
    if method != "GET":
        return "admin"
    return primary_role(roles)
