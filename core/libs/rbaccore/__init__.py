"""Shared RBAC core — the single source of truth for role semantics.

Both api-gateway (enforcement) and identity-service (seed) import this, so the
built-in role permission docs and the merge/check logic exist in exactly ONE
place. Stdlib-only and pure (except nothing) so tests load it directly.

Permission document shape (per role, v2):
    {
      "all": bool,                         # superadmin-style: allow everything
      "flags": ["reveal", ...],            # capability flags (credential reveal, ...)
      "perms": {"<segment>": ["GET","POST","PUT","PATCH","DELETE"] | ["*"], ...}
    }

Roles answer CAPABILITY (which feature/verb). Resource access (which host, which
credential) is a SEPARATE mandatory gate in za_authorizations — see bypasses_authz.

v3 (4-role rewrite): exactly 4 roles — superadmin, admin, support, user — each
mapped to real backend segments (services/api-gateway/app/proxy.py SERVICE_ROUTES
is the authoritative segment list; every real segment appears in a group below,
nothing silently falls through to default-deny by omission). "playbook-run" is a
pseudo-segment: not a real routed segment, only meaningful via the special-case
check in services/api-gateway/app/rbac.py::is_authorized() that separates
"run an existing playbook" from "create/edit a playbook template" — both are
POST on the same real `job-templates` segment, so segment+method alone can't
tell them apart.
"""

from __future__ import annotations

ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]

# Highest privilege first — primary role + display precedence.
PRECEDENCE = ["superadmin", "admin", "support", "user"]

# Roles that BYPASS the mandatory za_authorization resource gate (break-glass).
# support/user are default-deny on resource actions (ssh connect, credential
# reveal, run-job) and need an explicit grant, even though support has broad
# CRUD capability over the underlying inventory/credential/playbook records.
_AUTHZ_BYPASS = frozenset({"superadmin", "admin"})

# Segment groups (upstream path first segment) — every real segment from
# proxy.SERVICE_ROUTES appears in exactly one group below.
_INVENTORY = ["hosts", "host-groups", "credentials", "credential-templates"]
# Zone/gateway management (ProxyJump chain topology) is admin-only — a support
# user can be authorized against hosts inside a zone without being able to
# reshape the zone's routing, same tier as _SYSTEM segments.
_ZONES = ["zones"]
_AUTOMATION = ["projects", "job-templates", "schedules", "job-runs",
               "secret-management-jobs", "trigger-bindings", "triggers"]
_SYSTEM = ["log-backend", "settings", "api-tokens", "command-filters",
           "command-groups", "login-acls", "housekeeping"]  # excluded from admin + support
_SELF = ["ssh", "recordings", "metrics"]  # "auth" is always-allowed, not listed here


def _grant(segments, methods=ALL_METHODS):
    return {s: list(methods) for s in segments}


# ── Built-in role permission docs — the ONE definition (seed + gateway fallback) ──
BUILTIN_ROLE_PERMS: dict[str, dict] = {
    "superadmin": {"all": True, "flags": ["reveal"], "perms": {}},

    "admin": {
        "flags": ["reveal", "mfa"],
        "perms": {
            **_grant(_INVENTORY + _ZONES + _AUTOMATION + _SELF),
            "authorizations": list(ALL_METHODS),
            "access-reviews": list(ALL_METHODS),  # PCI DSS Phase C — can revoke access, admin-only (not support)
            "audit": ["GET"],
            "users": ["GET", "PUT"],   # edit existing users; cannot create (POST)
            "roles": ["GET"],          # view only; cannot create/edit role definitions
        },
    },

    "support": {
        "flags": [],
        "perms": {
            **_grant(_INVENTORY + _AUTOMATION),
            "authorizations": list(ALL_METHODS),
            "ssh": ["GET", "POST", "DELETE"],
            "recordings": ["GET"],
            "metrics": ["GET"],
            "users": ["GET", "PUT"],   # same restricted scope as admin
            "roles": ["GET"],
        },
    },

    "user": {
        "flags": [],
        "perms": {
            "hosts": ["GET"],
            "ssh": ["GET", "POST", "DELETE"],
            "job-runs": ["GET"],
            "job-templates": ["GET"],
            "playbook-run": ["POST"],   # pseudo-segment: only .../job-templates/{id}/run
            "recordings": ["GET"],
        },
    },
}

BUILTIN_ROLE_NAMES = frozenset(BUILTIN_ROLE_PERMS)


# ── Pure helpers (unit-tested directly, no DB) ────────────────────────────────
def merge_roles(direct: list[str], group_roles: list[str]) -> list[str]:
    """Effective role set = direct ∪ group-granted, deduped, ordered by precedence
    (unknown roles appended in first-seen order). This is THE union — every auth
    path (JWT mint, live-refresh, SSO) must route through it."""
    seen: dict[str, None] = {}
    for r in list(direct or []) + list(group_roles or []):
        if r and r not in seen:
            seen[r] = None
    names = list(seen)
    ranked = [r for r in PRECEDENCE if r in seen]
    tail = [r for r in names if r not in PRECEDENCE]
    return ranked + tail


def primary_role(roles: list[str]) -> str:
    for r in PRECEDENCE:
        if r in roles:
            return r
    return roles[0] if roles else "user"


def role_allows(doc: dict | None, method: str, seg: str) -> bool:
    """Does ONE role's permission doc permit method on segment? Pure."""
    if not doc:
        return False
    if doc.get("all"):
        return True
    methods = (doc.get("perms") or {}).get(seg) or (doc.get("perms") or {}).get("*")
    if not methods:
        return False
    return method in methods or "*" in methods


def doc_can_reveal(doc: dict | None) -> bool:
    if not doc:
        return False
    return bool(doc.get("all")) or "reveal" in (doc.get("flags") or [])


def doc_can_mfa(doc: dict | None) -> bool:
    if not doc:
        return False
    return bool(doc.get("all")) or "mfa" in (doc.get("flags") or [])


def bypasses_authz(roles: list[str]) -> bool:
    """True if the caller skips the mandatory za_authorization resource gate.
    ONLY superadmin/admin (break-glass). support/user are default-deny on
    resource actions even when their role grants the capability."""
    return any(r in _AUTHZ_BYPASS for r in (roles or []))


def authz_bypass_role_names() -> frozenset[str]:
    """The role-name set bypasses_authz checks against, for callers that need
    to filter/query by role name directly (e.g. a bulk DB query resolving
    which users hold a bypass role) rather than evaluate one user's roles at a
    time — keeps the single definition of "bypass role" in this module."""
    return _AUTHZ_BYPASS
