"""SeyalRun MCP server — HTTP transport for the tool/resource layer in rpc.py.

Streamable-HTTP: JSON-RPC 2.0 over POST /mcp. The agent authenticates with its
scoped Personal Access Token (``Authorization: Bearer sr_...``); that exact token is
forwarded to the api-gateway by each tool/resource, so the gateway does all the
authorization and auditing. This module is only the wire.

Auth is advertised the MCP/OAuth way: a request with no bearer token gets a 401 with
a ``WWW-Authenticate`` header pointing at the protected-resource metadata, so a
client can discover where to get a token (SeyalRun issues PATs from Admin → Security).
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from .rpc import TOOLS, handle

app = FastAPI(title="SeyalRun MCP Server")


def _origin(request: Request) -> str:
    """Public origin as seen by the client — honour the edge proxy's forwarded
    headers so the advertised URLs are the external https ones, not the internal."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


@app.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata(request: Request):
    origin = _origin(request)
    return {
        "resource": f"{origin}/mcp",
        # SeyalRun is the authority — PATs are minted in Admin → Security.
        "authorization_servers": [origin],
        "bearer_methods_supported": ["header"],
    }


def _unauthorized(request: Request) -> JSONResponse:
    meta = f'{_origin(request)}/.well-known/oauth-protected-resource'
    return JSONResponse(
        status_code=401,
        content={"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32001, "message": "authentication required: send Authorization: Bearer sr_<token>"}},
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{meta}"'},
    )


@app.post("/mcp")
async def mcp_endpoint(request: Request, authorization: Optional[str] = Header(default=None)):
    pat = (
        authorization.removeprefix("Bearer ").strip()
        if authorization and authorization.startswith("Bearer ")
        else None
    )
    if not pat:
        return _unauthorized(request)
    payload = await request.json()
    if isinstance(payload, list):  # JSON-RPC batch
        out = [r for m in payload if (r := await handle(m, pat)) is not None]
        return JSONResponse(out)
    resp = await handle(payload, pat)
    if resp is None:  # notification — no response body
        return JSONResponse(status_code=202, content=None)
    return JSONResponse(resp)


@app.get("/health")
async def health():
    return {"status": "ok", "tools": len(TOOLS)}
