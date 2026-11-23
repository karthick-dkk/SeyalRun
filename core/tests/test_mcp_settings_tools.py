"""The MCP tool surface on the wire: posture read tools, the run_automation request
shape, the X-MCP-Tool tag the gateway keys its limit/audit on, and the MCP page's
tool table staying in step with the real registry.

Requests are executed against an httpx MockTransport, so what is asserted is the
request mcp-server actually sends, not a description of it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
from pathlib import Path

import httpx
import pytest

from libs.apiscopes import AGENT_GRANTABLE, required_scope

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("mcp_rpc_tools", ROOT / "services/mcp-server/app/rpc.py")
mcp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp)

PAT = "sr_tooltest"


@pytest.fixture
def wire(monkeypatch):
    """Route mcp-server's gateway calls to a handler; record every request."""
    seen: list[httpx.Request] = []
    routes: dict[str, tuple[int, object]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, body = routes.get(request.url.path, (200, {}))
        return httpx.Response(status, json=body)

    real = httpx.AsyncClient

    def client(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(mcp.httpx, "AsyncClient", client)
    routes["/api/v1/mcp/policy"] = (200, {"enabled": True, "allow_write_tools": True, "calls_per_minute": 60})
    mcp._reset_policy_cache()
    yield seen, routes
    mcp._reset_policy_cache()


def _call(name, args=None):
    return asyncio.run(mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": name, "arguments": args or {}}}, PAT))


def _tool_requests(seen):
    return [r for r in seen if r.url.path != "/api/v1/mcp/policy"]


def test_fifteen_tools_including_posture():
    names = [t["name"] for t in mcp.TOOLS]
    assert len(names) == 15 and len(set(names)) == 15
    assert {"get_posture_summary", "list_findings"} <= set(names)


def test_posture_tools_need_posture_read_which_matches_the_gateway():
    for name, path in (("get_posture_summary", "posture/summary"),
                       ("list_findings", "posture/findings")):
        t = mcp._TOOLS_BY_NAME[name]
        assert t["scope"] == "posture:read" in AGENT_GRANTABLE and t["kind"] == "read"
        assert required_scope("posture", "GET", path) == t["scope"]
    assert required_scope("posture", "GET", "posture/findings/summary") == "posture:read"


def test_get_posture_summary_combines_both_reads(wire):
    seen, routes = wire
    routes["/api/v1/posture/summary"] = (200, {"hosts_total": 4, "findings_open": 9})
    routes["/api/v1/posture/findings/summary"] = (200, {"open_total": 9, "kev_open": 2})
    r = _call("get_posture_summary")["result"]
    assert r["isError"] is False
    data = json.loads(r["content"][0]["text"])
    assert data == {"summary": {"hosts_total": 4, "findings_open": 9},
                    "findings": {"open_total": 9, "kev_open": 2}}
    reqs = _tool_requests(seen)
    assert [q.url.path for q in reqs] == ["/api/v1/posture/summary", "/api/v1/posture/findings/summary"]
    # one tool call = one MCP tag (one rate-limit hit, one audit row)
    assert reqs[0].headers["x-mcp-tool"] == "get_posture_summary"
    assert "x-mcp-tool" not in reqs[1].headers
    assert all(q.headers["authorization"] == f"Bearer {PAT}" for q in reqs)


def test_get_posture_summary_reports_a_failed_part(wire):
    seen, routes = wire
    routes["/api/v1/posture/summary"] = (200, {"hosts_total": 4})
    routes["/api/v1/posture/findings/summary"] = (403, {"detail": "forbidden"})
    r = _call("get_posture_summary")["result"]
    data = json.loads(r["content"][0]["text"])
    assert r["isError"] is True
    assert data["summary"] == {"hosts_total": 4} and data["findings"]["error"] == "gateway 403"


def test_get_posture_summary_stops_on_first_failure(wire):
    seen, routes = wire
    routes["/api/v1/posture/summary"] = (403, {"detail": "token scope does not permit"})
    r = _call("get_posture_summary")["result"]
    assert r["isError"] is True and "token scope" in r["content"][0]["text"]
    assert [q.url.path for q in _tool_requests(seen)] == ["/api/v1/posture/summary"]


def test_list_findings_query_mapping(wire):
    seen, _ = wire
    _call("list_findings", {"host": "h-1", "severity": "critical", "kev": True, "status": "open", "limit": 500})
    (req,) = _tool_requests(seen)
    assert req.method == "GET" and req.url.path == "/api/v1/posture/findings"
    assert dict(req.url.params) == {"host_id": "h-1", "severity": "critical", "kev": "true",
                                    "status": "open", "limit": "100"}
    assert req.headers["x-mcp-tool"] == "list_findings"


def test_list_findings_defaults_and_bad_limit(wire):
    seen, _ = wire
    _call("list_findings", {})
    _call("list_findings", {"limit": "lots", "kev": False, "severity": None})
    a, b = _tool_requests(seen)
    assert dict(a.url.params) == {"limit": "50"}
    assert dict(b.url.params) == {"limit": "50", "kev": "false"}


def test_run_automation_sends_the_runpayload_shape(wire):
    seen, _ = wire
    _call("run_automation", {"template_id": "T1", "host_ids": ["h1"], "extra_vars": {"svc": "nginx"}})
    (req,) = _tool_requests(seen)
    assert req.method == "POST" and req.url.path == "/api/v1/job-templates/T1/run"
    assert json.loads(req.content) == {"target_host_ids": ["h1"], "params": {"svc": "nginx"}}
    assert req.headers["x-mcp-tool"] == "run_automation"


def test_whoami_and_resources_are_tagged_too(wire):
    seen, _ = wire
    _call("whoami")
    asyncio.run(mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/read",
                            "params": {"uri": "seyalrun://inventory/hosts"}}, PAT))
    tags = {q.url.path: q.headers.get("x-mcp-tool") for q in _tool_requests(seen)}
    assert tags == {"/api/v1/auth/session": "whoami", "/api/v1/hosts": "read_resource"}


def test_policy_is_read_with_the_callers_token(wire):
    seen, routes = wire
    routes["/api/v1/mcp/policy"] = (200, {"enabled": False})
    r = _call("list_hosts")
    assert r["error"]["code"] == mcp.ERR_POLICY
    (pol,) = seen
    assert pol.url.path == "/api/v1/mcp/policy" and pol.headers["authorization"] == f"Bearer {PAT}"


def test_mcp_page_tool_table_matches_the_registry():
    vue = (ROOT / "services/frontend/src/views/McpView.vue").read_text()
    block = vue.split("const TOOLS = [", 1)[1].split("\n]", 1)[0]
    rows = re.findall(r"scope: '([^']+)', tools: \[([^\]]*)\], kind: '(\w+)'", block)
    assert rows, "found no tool rows in McpView.vue"
    page = {}
    for scope, tools, kind in rows:
        for name in re.findall(r"'([a-z_]+)'", tools):
            page[name] = (None if scope == "always" else scope, kind)
    real = {t["name"]: (t["scope"], "Read" if t["kind"] == "read" else "Change/Run") for t in mcp.TOOLS}
    assert set(page) == set(real)
    for name, (scope, kind) in page.items():
        assert scope == real[name][0], name
        assert (kind == "Read") == (real[name][1] == "Read"), name


def test_composite_reads_call_served_routes_too():
    """test_mcp_route_contract checks each tool's primary path; the extra GETs of a
    composite tool (get_posture_summary) must be real routes as well."""
    from test_mcp_route_contract import _served
    extra = [(t["name"], p) for t in mcp.TOOLS for _k, p in t["also"]]
    assert extra
    for name, path in extra:
        assert _served("GET", path), f"{name} also reads GET {path}, which is not served"
