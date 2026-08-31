"""MCP protocol + tool registry for the SeyalRun MCP server — transport-free.

Every tool is a thin, typed wrapper over a SeyalRun REST endpoint. The agent's
scoped Personal Access Token is forwarded to the api-gateway on every call, so this
layer authorizes NOTHING itself: the gateway enforces ``scopes ∩ role ∩
authorization`` and writes the audit row. A tool the token lacks the scope for comes
back as a gateway 403, surfaced as a tool error. The MCP server never sees a secret.

Kept free of FastAPI so the tool contract and JSON-RPC handling are unit-testable
without importing the HTTP transport (main.py).
"""

from __future__ import annotations

import os

import httpx

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://api-gateway:8000")
PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "seyalrun", "version": "2.0-beta"}


def _tool(name, scope, method, path, description, properties=None, required=None, query=None):
    # scope=None marks an always-available tool (no scope gate — e.g. whoami, which
    # any authenticated token may call to introspect itself).
    tail = "always available" if scope is None else f"requires scope: {scope}"
    return {
        "name": name, "scope": scope, "method": method, "path": path,
        "query": set(query or []),
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
          "Run an allowlisted automation job-template against hosts. Only templates an "
          "admin pre-approved are runnable; params are filtered server-side.",
          {"template_id": {"type": "string"},
           "host_ids": {"type": "array", "items": {"type": "string"}},
           "extra_vars": {"type": "object"}},
          ["template_id"]),
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
    _tool("ack_notification", "notifications:ack", "POST", "/api/v1/notifications/{notification_id}/ack",
          "Acknowledge a notification.",
          {"notification_id": {"type": "string"}}, ["notification_id"]),
]
_TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


def _public_tool(t: dict) -> dict:
    return {"name": t["name"], "description": t["description"], "inputSchema": t["schema"]}


def _result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _build_request(tool: dict, args: dict):
    """(method, path, query, body): substitute a {placeholder} in the path, route
    declared query args to params, everything else to the body. Pure/no network."""
    path = tool["path"]
    body: dict = {}
    params: dict = {}
    for key, val in (args or {}).items():
        placeholder = "{" + key + "}"
        if placeholder in path:
            path = path.replace(placeholder, str(val))
        elif key in tool["query"]:
            params[key] = val
        else:
            body[key] = val
    return tool["method"], path, params, body


async def _call_tool(tool: dict, args: dict, pat):
    if not pat:
        return {"content": [{"type": "text", "text": "no API token — send Authorization: Bearer sr_..."}],
                "isError": True}
    method, path, params, body = _build_request(tool, args)
    kwargs = {"headers": {"Authorization": f"Bearer {pat}"}, "params": params}
    if method != "GET":
        kwargs["json"] = body
    try:
        async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30) as client:
            resp = await client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        return {"content": [{"type": "text", "text": f"gateway unreachable: {exc}"}], "isError": True}
    return {"content": [{"type": "text", "text": resp.text}], "isError": resp.status_code >= 400}


async def handle(msg: dict, pat):
    """One JSON-RPC message -> response dict, or None for a notification."""
    method = msg.get("method")
    req_id = msg.get("id")
    if method == "initialize":
        return _result(req_id, {"protocolVersion": PROTOCOL_VERSION,
                                "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO})
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": [_public_tool(t) for t in TOOLS]})
    if method == "tools/call":
        params = msg.get("params") or {}
        tool = _TOOLS_BY_NAME.get(params.get("name"))
        if tool is None:
            return _error(req_id, -32602, f"unknown tool: {params.get('name')}")
        return _result(req_id, await _call_tool(tool, params.get("arguments") or {}, pat))
    return _error(req_id, -32601, f"method not found: {method}")
