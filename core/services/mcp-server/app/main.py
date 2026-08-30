"""SeyalRun MCP server — HTTP transport for the tool layer in rpc.py.

Streamable-HTTP: JSON-RPC 2.0 over POST /mcp. The agent authenticates with its
scoped Personal Access Token (``Authorization: Bearer sr_...``); that exact token is
forwarded to the api-gateway by each tool (see rpc.py), so the gateway does all the
authorization and auditing. This module is only the wire.
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from .rpc import TOOLS, handle

app = FastAPI(title="SeyalRun MCP Server")


@app.post("/mcp")
async def mcp_endpoint(request: Request, authorization: Optional[str] = Header(default=None)):
    pat = (
        authorization.removeprefix("Bearer ").strip()
        if authorization and authorization.startswith("Bearer ")
        else None
    )
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
