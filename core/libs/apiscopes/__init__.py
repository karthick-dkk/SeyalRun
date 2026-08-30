"""Fine-grained API-token (PAT) scopes for programmatic / AI-agent access.

A Personal Access Token carries a list of scopes. The api-gateway enforces, per
request, that the token's scopes permit ``(segment, method)`` — ON TOP OF the
caller's role perms (rbaccore) and, downstream, za_authorization. So a scoped
token can only ever do LESS than the human who owns it: effective access is
``scopes ∩ role ∩ authorization``. Nothing here can widen access.

Scope shape is ``<domain>:<action>``. A domain maps to one or more routed
segments (api-gateway SERVICE_ROUTES); the action is one of read/write/run/ack.

Two hard lines for a PAM:
  * ``credentials:*`` is never in :data:`AGENT_GRANTABLE`, so an agent token can
    never read a secret. It may still *use* a credential to open a session (if it
    holds ``sessions:open``) — terminal-service unwraps the secret with its own
    service identity, the token never sees it.
  * ``admin:*`` (users, roles, authorizations, token issuance, settings) is never
    agent-grantable either — no agent administers identity or access.

Backward compatibility: tokens issued before fine-grained scopes carry the coarse
``read`` / ``write`` scopes. Those keep working (``read`` satisfies any read,
``write`` any write) so this change breaks no existing token; only the new
per-domain scopes are precise.
"""

from __future__ import annotations

READ_METHODS = frozenset({"GET", "HEAD"})

# segment -> (read_scope, write_scope). The write_scope of a segment an agent must
# never mutate is an ``admin:write`` sentinel: real, but not agent-grantable.
_SEGMENT_SCOPES: dict[str, tuple[str, str]] = {
    # inventory
    "hosts": ("inventory:read", "inventory:write"),
    "host-groups": ("inventory:read", "inventory:write"),
    "zones": ("inventory:read", "inventory:write"),
    "credential-templates": ("inventory:read", "inventory:write"),
    # credentials: neither scope is agent-grantable — secrets are off-limits to PATs
    "credentials": ("credentials:read", "credentials:write"),
    # automation
    "projects": ("automation:read", "automation:write"),
    "job-templates": ("automation:read", "automation:write"),
    "schedules": ("automation:read", "automation:write"),
    "job-runs": ("automation:read", "automation:write"),
    "secret-management-jobs": ("automation:read", "automation:write"),
    "housekeeping": ("automation:read", "automation:write"),
    "test-connection": ("automation:read", "automation:write"),
    # sessions / terminal
    "ssh": ("sessions:open", "sessions:open"),
    "sftp": ("sessions:open", "sessions:open"),
    "recordings": ("sessions:read", "admin:write"),
    # observability
    "audit": ("audit:read", "admin:write"),          # append-only; no PAT write
    "metrics": ("metrics:read", "admin:write"),
    "notifications": ("notifications:read", "notifications:ack"),
    "triggers": ("automation:read", "admin:write"),
    # admin-only domains — read and write both gated to admin:* (not agent-grantable)
    "trigger-bindings": ("admin:read", "admin:write"),
    "users": ("admin:read", "admin:write"),
    "roles": ("admin:read", "admin:write"),
    "authorizations": ("admin:read", "admin:write"),
    "access-reviews": ("admin:read", "admin:write"),
    "command-groups": ("admin:read", "admin:write"),
    "command-filters": ("admin:read", "admin:write"),
    "login-acls": ("admin:read", "admin:write"),
    "api-tokens": ("admin:read", "admin:write"),
    "settings": ("admin:read", "admin:write"),
    "log-backend": ("admin:read", "admin:write"),
}

# The ONLY scopes an admin may put on an agent/PAT. Everything else
# (credentials:*, admin:*) is refused at issuance AND unsatisfiable at the gateway.
AGENT_GRANTABLE: frozenset[str] = frozenset({
    # reads
    "inventory:read", "automation:read", "sessions:read",
    "audit:read", "metrics:read", "notifications:read",
    # actions
    "automation:run", "inventory:write", "sessions:open", "notifications:ack",
})

# Human-facing catalog (label + risk) for the issuance UI. Order is display order.
SCOPE_CATALOG: list[dict] = [
    {"scope": "inventory:read", "label": "Read inventory", "kind": "read"},
    {"scope": "automation:read", "label": "Read automation", "kind": "read"},
    {"scope": "sessions:read", "label": "Read session history", "kind": "read"},
    {"scope": "audit:read", "label": "Read audit log", "kind": "read"},
    {"scope": "metrics:read", "label": "Read metrics", "kind": "read"},
    {"scope": "notifications:read", "label": "Read notifications", "kind": "read"},
    {"scope": "automation:run", "label": "Run allowlisted automation", "kind": "action"},
    {"scope": "inventory:write", "label": "Manage inventory (hosts/zones/groups)", "kind": "action"},
    {"scope": "sessions:open", "label": "Open SSH/SFTP sessions", "kind": "action"},
    {"scope": "notifications:ack", "label": "Acknowledge notifications", "kind": "action"},
]


def _is_run_pseudo_segment(segment: str, method: str, path: str) -> bool:
    """POST .../job-templates/{id}/run is a distinct capability from editing a
    template, so it gets its own scope (automation:run) rather than
    automation:write — mirrors rbaccore's playbook-run pseudo-segment."""
    return segment == "job-templates" and method == "POST" and path.rstrip("/").endswith("/run")


def required_scope(segment: str, method: str, path: str) -> str | None:
    """The single scope a PAT must hold to call ``(segment, method)``.

    Returns None when no scope can satisfy the request (an unmapped segment —
    deny by default, so a new route is closed to PATs until it is classified)."""
    if _is_run_pseudo_segment(segment, method, path):
        return "automation:run"
    pair = _SEGMENT_SCOPES.get(segment)
    if pair is None:
        return None
    read_scope, write_scope = pair
    return read_scope if method.upper() in READ_METHODS else write_scope


def scope_allows(scopes: list[str], segment: str, method: str, path: str) -> bool:
    """Does a token holding ``scopes`` permit ``(segment, method)``?

    Only meaningful for PAT callers — a session/cookie caller carries no scopes
    and must not be run through this (it is governed by role alone). Legacy coarse
    ``read``/``write`` scopes are honoured so pre-existing tokens do not break.
    """
    is_read = method.upper() in READ_METHODS
    if is_read and "read" in scopes:
        return True
    if not is_read and "write" in scopes:
        return True
    need = required_scope(segment, method, path)
    return need is not None and need in scopes


def validate_agent_scopes(scopes: list[str]) -> list[str]:
    """Reject anything an agent token may not hold. Returns the offending scopes
    (empty list => all fine). Callers refuse issuance when this is non-empty."""
    return [s for s in scopes if s not in AGENT_GRANTABLE and s not in ("read", "write")]
