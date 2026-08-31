"""The MCP server: its tool surface, the JSON-RPC handling, and the one invariant
that keeps it honest — every tool it exposes requires a scope that is actually
agent-grantable, so the MCP front door can never offer a capability outside the
agreed boundary (credentials/admin can't sneak in as a tool).

Loads the real service module by path and executes the pure/handler logic. The
network hop (tool -> gateway) is not exercised here; that is what the live deploy
check does. What matters here is the contract: scopes, shapes, and argument mapping.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

from libs.apiscopes import AGENT_GRANTABLE

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "mcp_rpc", ROOT / "services/mcp-server/app/rpc.py"
)
mcp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp)


def _run(msg, pat=None):
    return asyncio.run(mcp.handle(msg, pat))


# ── the invariant: the tool surface stays inside the agent scope catalog ─────

def test_every_tool_requires_an_agent_grantable_scope():
    for t in mcp.TOOLS:
        if t["scope"] is None:
            continue  # always-available introspection tool (whoami)
        assert t["scope"] in AGENT_GRANTABLE, (
            f"tool {t['name']} needs {t['scope']}, which is not agent-grantable — "
            "the MCP surface must never expose a capability outside the catalog"
        )


def test_whoami_is_always_available_and_maps_to_auth_session():
    w = mcp._TOOLS_BY_NAME["whoami"]
    assert w["scope"] is None                    # no scope gate
    assert w["path"] == "/api/v1/auth/session"    # relays the gateway's own identity
    assert "always available" in w["description"]


# ── MCP protocol basics ──────────────────────────────────────────────────────

def test_initialize_advertises_tools_resources_and_server():
    r = _run({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r["result"]["protocolVersion"]
    assert "tools" in r["result"]["capabilities"]
    assert "resources" in r["result"]["capabilities"]
    assert r["result"]["serverInfo"]["name"] == "seyalrun"


def test_resources_list_and_scope_backing():
    r = _run({"jsonrpc": "2.0", "id": 9, "method": "resources/list"})
    res = r["result"]["resources"]
    uris = {x["uri"] for x in res}
    assert "seyalrun://inventory/hosts" in uris and "seyalrun://audit/recent" in uris
    for x in res:
        assert x["uri"].startswith("seyalrun://") and x["mimeType"] == "application/json"
    # every resource is backed by an agent-grantable read scope
    for rsc in mcp.RESOURCES:
        assert rsc["scope"] in AGENT_GRANTABLE and rsc["scope"].endswith(":read")


def test_resources_read_unknown_and_no_token():
    r1 = _run({"jsonrpc": "2.0", "id": 10, "method": "resources/read",
               "params": {"uri": "seyalrun://nope"}}, pat="sr_x")
    assert "error" in r1
    r2 = _run({"jsonrpc": "2.0", "id": 11, "method": "resources/read",
               "params": {"uri": "seyalrun://inventory/hosts"}}, pat=None)
    assert "error" in r2 and "token" in r2["error"]["message"].lower()


def test_tools_list_returns_declared_tools_with_schema():
    r = _run({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = r["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"whoami", "list_hosts", "run_automation", "query_audit", "create_host", "ack_notification"} <= names
    for t in tools:
        assert t["inputSchema"]["type"] == "object"
        assert ("requires scope:" in t["description"]) or ("always available" in t["description"])


def test_notifications_are_not_answered():
    assert _run({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_tool_and_unknown_method_error():
    r1 = _run({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
               "params": {"name": "does_not_exist"}}, pat="sr_x")
    assert "error" in r1
    r2 = _run({"jsonrpc": "2.0", "id": 4, "method": "bogus/method"})
    assert "error" in r2


def test_tool_call_without_token_is_an_error_not_a_crash():
    r = _run({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
              "params": {"name": "list_hosts", "arguments": {}}}, pat=None)
    assert r["result"]["isError"] is True
    assert "token" in r["result"]["content"][0]["text"].lower()


# ── argument mapping (pure) ──────────────────────────────────────────────────

def test_build_request_substitutes_path_placeholder_and_bodies_the_rest():
    tool = mcp._TOOLS_BY_NAME["run_automation"]
    method, path, params, body = mcp._build_request(tool, {"template_id": "T1", "host_ids": ["h1"]})
    assert method == "POST"
    assert path == "/api/v1/job-templates/T1/run"   # {template_id} substituted
    assert body == {"host_ids": ["h1"]}             # non-path, non-query -> body
    assert params == {}


def test_build_request_routes_declared_query_args_to_params():
    tool = mcp._TOOLS_BY_NAME["query_audit"]
    _, path, params, body = mcp._build_request(tool, {"limit": 10})
    assert path == "/api/v1/audit/logs"
    assert params == {"limit": 10}   # limit is declared query, not body
    assert body == {}
