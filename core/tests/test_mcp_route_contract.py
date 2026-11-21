"""Every MCP tool and resource must call a route the target service really serves.

`ack_notification` shipped calling POST /api/v1/notifications/{id}/ack while
automation-service only ever exposed POST /notifications/{id}/read, so the tool
failed at runtime (architecture review F-12). test_mcp_server.py checks the
tool's scope and argument mapping, but nothing checked that the path exists, and
the mismatch spans three codebases: the MCP tool table, the gateway's segment
routing, and the callee's router declarations.

A source-shape check, like test_internal_route_paths.py: resolve each tool's
first segment through the gateway's SERVICE_ROUTES, then look for a matching
method + path among the route decorators of that service.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "services"

_spec = importlib.util.spec_from_file_location("mcp_rpc_contract", SERVICES / "mcp-server/app/rpc.py")
mcp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp)


def _gateway_routes() -> dict[str, str]:
    """first /api/v1 segment -> upstream service directory name"""
    src = (SERVICES / "api-gateway/app/proxy.py").read_text()
    block = src.split("SERVICE_ROUTES", 1)[1].split("}", 1)[0]
    return dict(re.findall(r'"([\w-]+)":\s*\(\s*"\w+",\s*"([\w-]+)"\s*\)', block))


_DECORATOR = re.compile(r'@(router|app)\.(get|post|put|patch|delete)\(\s*"([^"]*)"')
_ROUTER_PREFIX = re.compile(r'^router\s*=\s*APIRouter\((.*)$', re.M)


def _service_routes(service: str) -> set[tuple[str, str]]:
    """(METHOD, full path) for every route declared in a service.

    Every service mounts its api routers under /api/v1 (see each main.py); routes
    declared straight on `app` carry their full path already."""
    routes: set[tuple[str, str]] = set()
    for f in (SERVICES / service / "app").rglob("*.py"):
        src = f.read_text()
        prefix = ""
        m = _ROUTER_PREFIX.search(src)
        if m:
            p = re.search(r'prefix="([^"]*)"', m.group(1))
            prefix = p.group(1) if p else ""
        for kind, method, path in _DECORATOR.findall(src):
            full = path if kind == "app" else f"/api/v1{prefix}{path}"
            routes.add((method.upper(), full.rstrip("/") or "/"))
    return routes


def _template_matches(a: str, b: str) -> bool:
    """Two route templates name the same path when every literal segment agrees;
    a `{param}` on either side matches any one segment."""
    sa, sb = a.strip("/").split("/"), b.strip("/").split("/")
    if len(sa) != len(sb):
        return False
    return all(x == y or x.startswith("{") or y.startswith("{") for x, y in zip(sa, sb))


def _matches_any(routes: set[tuple[str, str]], method: str, path: str) -> bool:
    return any(m == method and _template_matches(p, path.rstrip("/")) for m, p in routes)


def _served(method: str, path: str) -> bool:
    # The gateway answers a few paths itself (GET /api/v1/auth/session) before any
    # proxying happens; those never reach an upstream service.
    if _matches_any(_service_routes("api-gateway"), method, path):
        return True
    segment = path.removeprefix("/api/v1/").split("/", 1)[0]
    service = _gateway_routes().get(segment)
    assert service, f"{path}: gateway has no route for segment {segment!r}"
    return _matches_any(_service_routes(service), method, path)


def test_route_discovery_is_not_vacuous():
    routes = _service_routes("automation-service")
    assert ("POST", "/api/v1/notifications/{notif_id}/read") in routes, sorted(routes)[:20]
    assert _gateway_routes().get("notifications") == "automation-service"


@pytest.mark.parametrize("tool", mcp.TOOLS, ids=lambda t: t["name"])
def test_every_mcp_tool_calls_a_served_route(tool):
    assert _served(tool["method"], tool["path"]), (
        f"MCP tool {tool['name']} calls {tool['method']} {tool['path']}, "
        "which the upstream service does not serve"
    )


@pytest.mark.parametrize("res", mcp.RESOURCES, ids=lambda r: r["uri"])
def test_every_mcp_resource_reads_a_served_route(res):
    assert _served("GET", res["path"]), f"MCP resource {res['uri']} reads GET {res['path']}, which is not served"
