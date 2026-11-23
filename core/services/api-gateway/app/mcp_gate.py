"""Gateway-side MCP enforcement and attribution.

mcp-server relays every tool call to this gateway carrying the agent's own PAT
and an ``X-MCP-Tool: <tool name>`` header. For a PAT request carrying that
header the gateway:

  * re-checks the MCP switches (enabled / write tools) — defence in depth behind
    mcp-server's own check, and authoritative within this cache's few seconds;
  * enforces the per-token MCP rate limit ("Calls per token") in Redis, keyed by
    the token id, through the same sliding-window limiter as every other gateway
    limit. It lives here rather than in mcp-server so the internet-facing
    mcp-server never holds the Redis password (sessions and lockout counters
    live in that Redis);
  * writes one audit row per call — action ``mcp.tool_call``, attributed to the
    token's owner, with the token name, tool, HTTP status and refusal reason —
    so GET /audit/logs?source=mcp is the agents' activity log.

Separately, for EVERY PAT (header or not), starting a playbook run is held to the
admin's "Playbooks agents may run" list — a token cannot dodge the list by
calling the REST API directly instead of going through MCP.

The header is not a credential: omitting it only skips the MCP-specific limit and
audit row (the ordinary per-user limit, scope check and the service's own audit
still apply), and adding it only subjects the caller to them. Session callers are
never affected.

Framework-free (decisions are returned, not raised) so the tests load this file
directly; main.py turns a decision into an HTTP error.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from libs.mcpsettings import normalize, run_template_id, template_allowed

MCP_TOOL_HEADER = "x-mcp-tool"
AUDIT_ACTION = "mcp.tool_call"
RATE_WINDOW_SECONDS = 60
_READ = frozenset({"GET", "HEAD"})
_TOOL_RE = re.compile(r"[a-z][a-z0-9_]{0,35}")

Decision = tuple[int, str] | None
Limiter = Callable[[str, int, int], Awaitable[bool]]


def tool_from_headers(headers) -> str | None:
    """The MCP tool name a request is tagged with; None when untagged. A malformed
    value is still MCP traffic (it gets the limit and an audit row) — as "unknown"."""
    raw = headers.get(MCP_TOOL_HEADER)
    if raw is None:
        return None
    raw = raw.strip()
    return raw if _TOOL_RE.fullmatch(raw) else "unknown"


def rate_key(identity: dict) -> str:
    return f"mcp:{identity.get('token_id') or identity.get('user_id') or 'anon'}"


UNAVAILABLE: Decision = (503, "MCP settings are temporarily unavailable; try again shortly")

# Other ways a token could get a playbook run started: re-running or approving
# a run (any template), firing a Zabbix trigger by hand, and creating/editing a
# schedule or a trigger binding (both run playbooks later as the owner).
_RUN_ACTION_RE = re.compile(r"job-runs/[^/]+/(?:rerun|approve)/?|triggers/manual/?")
_DEFERRED_RE = re.compile(r"(?:schedules|trigger-bindings)(?:/[^/]+)?/?")


async def check_mcp_call(identity: dict, method: str, settings: dict | None, limiter: Limiter) -> Decision:
    """MCP-tagged PAT request -> None (allowed) or (status, reason).
    settings None = never loaded: refuse (fail closed)."""
    if settings is None:
        return UNAVAILABLE
    s = normalize(settings)
    if not s["enabled"]:
        return 403, "MCP connections are turned off by an administrator"
    if method.upper() not in _READ and not s["allow_write_tools"]:
        return 403, "MCP is read-only: tools that change things are turned off by an administrator"
    if not await limiter(rate_key(identity), s["calls_per_minute"], RATE_WINDOW_SECONDS):
        return 429, f"MCP rate limit exceeded: {s['calls_per_minute']} calls per minute per token"
    return None


def starts_run(method: str, path: str) -> bool:
    """Could (method, path) start a playbook run, now or later?"""
    m, p = method.upper(), path.strip("/")
    if run_template_id(m, p) is not None:
        return True
    if m == "POST" and _RUN_ACTION_RE.fullmatch(p):
        return True
    return m in ("POST", "PUT", "PATCH") and bool(_DEFERRED_RE.fullmatch(p))


def check_run_allowlist(identity: dict, method: str, path: str, settings: dict | None) -> Decision:
    """Any PAT starting a playbook run -> refused unless the template is on the
    agent list (no list configured = every enabled template, today's behaviour).
    Re-runs, approvals, manual triggers, schedule and binding writes can't be tied to one listed template here, so
    while a list is set they are refused for tokens. settings None = fail closed."""
    if not identity.get("is_pat") or not starts_run(method, path):
        return None
    if settings is None:
        return UNAVAILABLE
    tid = run_template_id(method, path)
    if tid is not None:
        if template_allowed(settings, tid):
            return None
        return 403, "this playbook is not on the list agents may run (MCP settings)"
    if normalize(settings)["agent_template_ids"] is None:
        return None
    return 403, "agent tokens cannot re-run, approve, trigger or schedule playbooks while a playbook list is set (MCP settings)"


def audit_entry(identity: dict, tool: str, method: str, path: str, status: int,
                reason: str | None, ip: str) -> dict:
    """The /internal/audit payload for one MCP tool call."""
    details = {
        "tool": tool,
        "token_id": identity.get("token_id") or "",
        "token_name": identity.get("token_name") or "",
        "method": method.upper(),
        "path": path,
        "status": int(status),
    }
    if reason:
        details["reason"] = str(reason)[:300]
    return {
        "user_id": identity.get("user_id"),
        "username": identity.get("username") or "",
        "action": AUDIT_ACTION,
        "resource_type": "mcp_tool",
        "resource_id": tool[:36],
        "details": details,
        "ip_address": ip or "",
        "result": "success" if status < 400 else "failure",
    }
