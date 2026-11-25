"""MCP protocol + tool registry for the SeyalRun MCP server — transport-free.

Every tool is a thin, typed wrapper over a SeyalRun REST endpoint. The agent's
scoped Personal Access Token is forwarded to the api-gateway on every call, so this
layer authorizes NOTHING itself: the gateway enforces ``scopes ∩ role ∩
authorization`` and writes the audit row. A tool the token lacks the scope for comes
back as a gateway 403, surfaced as a tool error. The MCP server never sees a secret.

The one thing this layer DOES decide is the admin's MCP switches (the Settings card
on the MCP page, stored as the ``mcp`` platform setting): with MCP turned off every
request is refused with a JSON-RPC error, and in read-only mode the tools that
change things are neither listed nor callable. The switches come from the gateway's
``GET /api/v1/mcp/policy`` (read with the caller's own PAT) and are cached for
POLICY_CACHE_SECONDS; on a read failure the last known value is kept, and with none
yet the defaults apply — which equal the behaviour before the switches existed.

The per-token rate limit and the playbook allowlist are enforced by the gateway (it
holds Redis; this internet-facing service holds no secret). Every tool call is
tagged ``X-MCP-Tool`` so the gateway can apply the limit and write the
``mcp.tool_call`` audit row.

Kept free of FastAPI so the tool contract and JSON-RPC handling are unit-testable
without importing the HTTP transport (main.py).
"""

from __future__ import annotations

import json
import os
import time

import httpx

from libs.mcpsettings import public_policy

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://api-gateway:8000")
PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "seyalrun", "version": "3.0"}
MCP_TOOL_HEADER = "X-MCP-Tool"
POLICY_CACHE_SECONDS = 5.0

# JSON-RPC server-defined error codes (-32000..-32099).
ERR_AUTH = -32001
ERR_RESOURCE = -32002
ERR_POLICY = -32003

DISABLED_MESSAGE = ("MCP connections are turned off by a SeyalRun administrator. "
                    "Personal access tokens still work for the REST API.")
READ_ONLY_MESSAGE = ("MCP is read-only: a SeyalRun administrator has turned off tools "
                     "that change things, so {name} is not available.")


def _tool(name, scope, method, path, description, properties=None, required=None, query=None,
          rename=None, clamp=None, also=None):
    # scope=None marks an always-available tool (no scope gate — e.g. whoami, which
    # any authenticated token may call to introspect itself).
    # rename: {tool arg -> request key}; clamp: {query arg -> (min, max, default)};
    # also: [(key, GET path)] combined with the first result (a composite read).
    tail = "always available" if scope is None else f"requires scope: {scope}"
    return {
        "name": name, "scope": scope, "method": method, "path": path,
        # Anything that is not a GET changes something — hidden and refused when an
        # admin turns "Allow tools that change things" off.
        "kind": "read" if method == "GET" else "write",
        "query": set(query or []),
        "rename": dict(rename or {}),
        "clamp": dict(clamp or {}),
        "also": list(also or []),
        "schema": {"type": "object", "properties": properties or {}, "required": required or []},
        "description": f"{description}  ({tail})",
    }


TOOLS = [
    _tool("whoami", None, "GET", "/api/v1/auth/session",
          "Who am I: this token's identity, role, and the exact scopes it holds. Call "
          "this first to learn which other tools you are allowed to use — each tool "
          "lists the scope it needs."),
    _tool("list_hosts", "inventory:read", "GET", "/api/v1/hosts",
          "List the hosts SeyalRun can broker sessions to."),
    _tool("get_host", "inventory:read", "GET", "/api/v1/hosts/{host_id}",
          "Get one host by id.",
          {"host_id": {"type": "string"}}, ["host_id"]),
    _tool("list_zones", "inventory:read", "GET", "/api/v1/zones",
          "List network zones (gateway / ProxyJump topology)."),
    _tool("create_host", "inventory:write", "POST", "/api/v1/hosts",
          "Register a new host. Requires at least one asset group.",
          {"name": {"type": "string"}, "ip": {"type": "string"},
           "port": {"type": "integer", "default": 22},
           "group_ids": {"type": "array", "items": {"type": "string"}},
           "zone_id": {"type": "string"}},
          ["name", "ip", "group_ids"]),
    _tool("list_automation_templates", "automation:read", "GET", "/api/v1/job-templates",
          "List automation job-templates (Ansible playbooks / scripts)."),
    _tool("list_job_runs", "automation:read", "GET", "/api/v1/job-runs",
          "List recent automation job runs and their status."),
    _tool("run_automation", "automation:run", "POST", "/api/v1/job-templates/{template_id}/run",
          "Run an enabled automation job-template against hosts. An admin can limit which "
          "templates agents may run (MCP settings); params are filtered against the "
          "template's own allowlist server-side, and runs that need approval wait for it.",
          {"template_id": {"type": "string"},
           "host_ids": {"type": "array", "items": {"type": "string"}},
           "extra_vars": {"type": "object"}},
          ["template_id"],
          # automation-service's RunPayload names these target_host_ids / params.
          # Sent under the tool's own names they were silently dropped by pydantic,
          # and the run went to the template's default targets instead.
          rename={"host_ids": "target_host_ids", "extra_vars": "params"}),
    _tool("query_audit", "audit:read", "GET", "/api/v1/audit/logs",
          "Query the tamper-evident audit log (who did what, when).",
          {"limit": {"type": "integer", "default": 50}}, query=["limit"]),
    _tool("get_metrics", "metrics:read", "GET", "/api/v1/metrics/dashboard",
          "Platform metrics dashboard (sessions, jobs, host health)."),
    _tool("list_sessions", "sessions:read", "GET", "/api/v1/recordings",
          "List recorded SSH sessions."),
    _tool("list_notifications", "notifications:read", "GET", "/api/v1/notifications",
          "List notifications / alerts.",
          {"limit": {"type": "integer", "default": 50}}, query=["limit"]),
    _tool("ack_notification", "notifications:ack", "POST", "/api/v1/notifications/{notification_id}/read",
          "Acknowledge a notification.",
          {"notification_id": {"type": "string"}}, ["notification_id"]),
    # ── posture (3.0) ──
    _tool("get_posture_summary", "posture:read", "GET", "/api/v1/posture/summary",
          "Security posture at a glance: hosts reporting, reboots and security updates "
          "pending, active vulnerability-feed version and age, plus open findings by "
          "severity, known-exploited (KEV) count and hosts affected. Only hosts the "
          "token's owner may see are counted.",
          also=[("findings", "/api/v1/posture/findings/summary")]),
    _tool("list_findings", "posture:read", "GET", "/api/v1/posture/findings",
          "List vulnerability findings (CVE, package, installed/fixed version, severity, "
          "CVSS, EPSS, KEV, risk score, status). Filter by host, severity, KEV or status; "
          "at most 100 per call.",
          {"host": {"type": "string", "description": "host id"},
           "severity": {"type": "string", "enum": ["critical", "high", "medium", "low", "unknown"]},
           "kev": {"type": "boolean", "description": "only known-exploited (CISA KEV) findings"},
           "status": {"type": "string", "enum": ["open", "fixed", "accepted"]},
           "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 100}},
          query=["host", "severity", "kev", "status", "limit"],
          rename={"host": "host_id"}, clamp={"limit": (1, 100, 50)}),
    # Read-only on purpose: creating and approving a rollout stay with people.
    _tool("list_patch_rollouts", "posture:read", "GET", "/api/v1/posture/patches",
          "List patch rollouts: status, why one is waiting or halted, waves, host counts, "
          "who requested and approved it.",
          {"status": {"type": "string", "enum": ["pending_approval", "rejected", "approved", "running", "paused",
                                                 "halted", "completed", "cancelled"]},
           "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100}},
          query=["status", "limit"], clamp={"limit": (1, 100, 20)}),
    _tool("get_patch_rollout", "posture:read", "GET", "/api/v1/posture/patches/{rollout_id}",
          "One patch rollout: per-host status, step, package changes, pre/post checks, "
          "errors, and the event history.",
          {"rollout_id": {"type": "string"}}, ["rollout_id"]),
]
_TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


# ── Resources ────────────────────────────────────────────────────────────────
# Read-only context an agent can pull by URI. Each maps to a gateway GET carrying
# the agent's PAT, so a resource the token lacks the scope for comes back as a
# gateway 403 — same enforcement as the tools.
RESOURCES = [
    {"uri": "seyalrun://inventory/hosts", "name": "Hosts",
     "description": "All hosts SeyalRun can broker sessions to.",
     "scope": "inventory:read", "path": "/api/v1/hosts"},
    {"uri": "seyalrun://inventory/zones", "name": "Zones",
     "description": "Network zones and gateway topology.",
     "scope": "inventory:read", "path": "/api/v1/zones"},
    {"uri": "seyalrun://automation/templates", "name": "Automation templates",
     "description": "Available automation job-templates.",
     "scope": "automation:read", "path": "/api/v1/job-templates"},
    {"uri": "seyalrun://audit/recent", "name": "Recent audit log",
     "description": "The tamper-evident audit log (most recent entries).",
     "scope": "audit:read", "path": "/api/v1/audit/logs"},
    {"uri": "seyalrun://metrics/dashboard", "name": "Metrics",
     "description": "Platform metrics dashboard.",
     "scope": "metrics:read", "path": "/api/v1/metrics/dashboard"},
]
_RESOURCES_BY_URI = {r["uri"]: r for r in RESOURCES}


# ── Admin MCP switches (policy) ──────────────────────────────────────────────
_policy: dict | None = None
_policy_at: float = 0.0


async def _fetch_policy(pat: str) -> dict | None:
    """GET the gateway's MCP policy with the caller's PAT; None on any failure."""
    try:
        async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=3) as client:
            resp = await client.get("/api/v1/mcp/policy", headers={"Authorization": f"Bearer {pat}"})
        if resp.status_code != 200:
            return None
        return public_policy(resp.json())
    except (httpx.HTTPError, ValueError):
        return None


async def get_policy(pat) -> dict:
    """Current switches: cached a few seconds; a failed read keeps the last known
    value; with none yet, the defaults (= behaviour before the switches existed)."""
    global _policy, _policy_at
    now = time.monotonic()
    stale = not _policy_at or now - _policy_at >= POLICY_CACHE_SECONDS
    if stale and pat:
        _policy_at = now  # before the await, so concurrent calls don't stampede
        fetched = await _fetch_policy(pat)
        if fetched is not None:
            _policy = fetched
    return dict(_policy) if _policy is not None else public_policy({})


def _reset_policy_cache() -> None:
    global _policy, _policy_at
    _policy, _policy_at = None, 0.0


def visible_tools(policy: dict) -> list[dict]:
    return [t for t in TOOLS if policy.get("allow_write_tools", True) or t["kind"] == "read"]


def _public_tool(t: dict) -> dict:
    return {"name": t["name"], "description": t["description"], "inputSchema": t["schema"]}


def _public_resource(r: dict) -> dict:
    return {"uri": r["uri"], "name": r["name"], "description": r["description"],
            "mimeType": "application/json"}


async def _read_resource(res: dict, pat):
    if not pat:
        raise _ToolAuthError("no API token — send Authorization: Bearer sr_...")
    try:
        async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30) as client:
            resp = await client.get(res["path"], headers={"Authorization": f"Bearer {pat}",
                                                          MCP_TOOL_HEADER: "read_resource"})
    except httpx.HTTPError as exc:
        raise _ToolAuthError(f"gateway unreachable: {exc}")
    if resp.status_code >= 400:
        raise _ToolAuthError(f"gateway {resp.status_code}: {resp.text[:200]}")
    return {"contents": [{"uri": res["uri"], "mimeType": "application/json", "text": resp.text}]}


class _ToolAuthError(Exception):
    pass


def _result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _clamp(val, lo: int, hi: int, default: int) -> int:
    try:
        n = int(val)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _query_value(val):
    # httpx would send True as "True"; FastAPI's bool parser wants true/false.
    return ("true" if val else "false") if isinstance(val, bool) else val


def _build_request(tool: dict, args: dict):
    """(method, path, query, body): substitute a {placeholder} in the path, route
    declared query args to params, everything else to the body — renaming per the
    tool's map and clamping bounded query args. Pure/no network."""
    path = tool["path"]
    body: dict = {}
    params: dict = {}
    rename = tool.get("rename", {})
    clamp = tool.get("clamp", {})
    for key, val in (args or {}).items():
        placeholder = "{" + key + "}"
        target = rename.get(key, key)
        if placeholder in path:
            path = path.replace(placeholder, str(val))
        elif key in tool["query"]:
            if val is None:
                continue
            if key in clamp:
                val = _clamp(val, *clamp[key])
            params[target] = _query_value(val)
        else:
            body[target] = val
    for key, (_lo, _hi, default) in clamp.items():
        params.setdefault(rename.get(key, key), default)
    return tool["method"], path, params, body


def _json_or_text(resp):
    try:
        return resp.json()
    except ValueError:
        return resp.text


def _combine(tool: dict, first, extra: list) -> dict:
    """A composite read: {"<first path's last segment>": ..., "<key>": ...}. Any
    failed part makes the whole call an error (the parts that worked are kept)."""
    out = {tool["path"].rstrip("/").rsplit("/", 1)[-1]: _json_or_text(first)}
    failed = False
    for key, resp in extra:
        if resp.status_code >= 400:
            failed = True
            out[key] = {"error": f"gateway {resp.status_code}", "detail": resp.text[:300]}
        else:
            out[key] = _json_or_text(resp)
    return {"content": [{"type": "text", "text": json.dumps(out)}], "isError": failed}


async def _call_tool(tool: dict, args: dict, pat):
    if not pat:
        return {"content": [{"type": "text", "text": "no API token — send Authorization: Bearer sr_..."}],
                "isError": True}
    method, path, params, body = _build_request(tool, args)
    # Only the primary request carries X-MCP-Tool: one tool call = one MCP rate-
    # limit hit and one audit row, even for a composite read.
    kwargs = {"headers": {"Authorization": f"Bearer {pat}", MCP_TOOL_HEADER: tool["name"]}, "params": params}
    if method != "GET":
        kwargs["json"] = body
    try:
        async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30) as client:
            resp = await client.request(method, path, **kwargs)
            extra = []
            if tool["also"] and resp.status_code < 400:
                for key, extra_path in tool["also"]:
                    extra.append((key, await client.get(extra_path, headers={"Authorization": f"Bearer {pat}"})))
    except httpx.HTTPError as exc:
        return {"content": [{"type": "text", "text": f"gateway unreachable: {exc}"}], "isError": True}
    if not tool["also"] or resp.status_code >= 400:
        return {"content": [{"type": "text", "text": resp.text}], "isError": resp.status_code >= 400}
    return _combine(tool, resp, extra)


async def handle(msg: dict, pat):
    """One JSON-RPC message -> response dict, or None for a notification."""
    method = msg.get("method")
    req_id = msg.get("id")
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    policy = await get_policy(pat)
    if not policy.get("enabled", True):
        # Every request, initialize included — the agent learns why at once.
        return _error(req_id, ERR_POLICY, DISABLED_MESSAGE)
    if method == "initialize":
        return _result(req_id, {"protocolVersion": PROTOCOL_VERSION,
                                "capabilities": {"tools": {}, "resources": {}},
                                "serverInfo": SERVER_INFO})
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": [_public_tool(t) for t in visible_tools(policy)]})
    if method == "resources/list":
        return _result(req_id, {"resources": [_public_resource(r) for r in RESOURCES]})
    if method == "resources/read":
        uri = (msg.get("params") or {}).get("uri")
        res = _RESOURCES_BY_URI.get(uri)
        if res is None:
            return _error(req_id, -32602, f"unknown resource: {uri}")
        try:
            return _result(req_id, await _read_resource(res, pat))
        except _ToolAuthError as exc:
            return _error(req_id, ERR_RESOURCE, str(exc))
    if method == "tools/call":
        params = msg.get("params") or {}
        tool = _TOOLS_BY_NAME.get(params.get("name"))
        if tool is None:
            return _error(req_id, -32602, f"unknown tool: {params.get('name')}")
        if tool["kind"] == "write" and not policy.get("allow_write_tools", True):
            return _error(req_id, ERR_POLICY, READ_ONLY_MESSAGE.format(name=tool["name"]))
        return _result(req_id, await _call_tool(tool, params.get("arguments") or {}, pat))
    return _error(req_id, -32601, f"method not found: {method}")
