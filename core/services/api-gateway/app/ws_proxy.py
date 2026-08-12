"""WebSocket reverse-proxy for terminal sessions.

Browsers cannot send Authorization headers on WS upgrade requests, so the
client JWT / PAT is passed as ?token= in the query string.  The proxy
authenticates it, mints a short-lived service token, and opens an upstream
WebSocket with the identity forwarded as X-* headers — bidirectional pump.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlencode

import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from libs.servicetoken import mint

from . import rbac
from .config import get_settings
from .security import AuthError, resolve_identity_from_token

# WS path segment → equivalent REST segment for the RBAC matrix check.
_WS_RBAC_SEGMENT = {"ssh": "ssh", "jobs": "job-runs", "notifications": "notifications"}

logger = logging.getLogger(__name__)

router = APIRouter()

# First WS path segment → (settings attr for upstream WS base URL, service-token audience)
WS_SERVICE_ROUTES: dict[str, tuple[str, str]] = {
    "ssh": ("terminal_service_ws_url", "terminal-service"),
    "jobs": ("automation_service_ws_url", "automation-service"),
    "notifications": ("automation_service_ws_url", "automation-service"),
}


@router.websocket("/ws/{path:path}")
async def ws_gateway(websocket: WebSocket, path: str):
    segment = path.split("/", 1)[0]
    route = WS_SERVICE_ROUTES.get(segment)
    if route is None:
        await websocket.close(code=4404)
        return

    settings = get_settings()
    base_attr, audience = route

    token = websocket.query_params.get("token", "")
    try:
        identity = await resolve_identity_from_token(token)
    except AuthError:
        await websocket.close(code=4401)
        return

    # Zero-trust: enforce the same RBAC matrix on the WS upgrade.
    roles = identity.get("roles") or [identity.get("role", "user")]
    if not rbac.is_authorized(roles, "GET", _WS_RBAC_SEGMENT.get(segment, segment)):
        await websocket.close(code=4403)
        return
    identity["role"] = rbac.primary_role(roles)   # forward real role (PAM correctness)

    await websocket.accept()

    service_token = mint("api-gateway", audience, settings.service_jwt_secret)
    # Forward terminal-size hint params (cols, rows) to upstream so the PTY is
    # created with the correct dimensions.  Strip the client's JWT token — it
    # must not be forwarded to internal services.
    fwd_params = {k: v for k, v in websocket.query_params.items() if k != "token"}
    upstream_url = f"{getattr(settings, base_attr)}/ws/{path}"
    if fwd_params:
        upstream_url += "?" + urlencode(fwd_params)
    extra_headers = {
        "X-Service-Token": service_token,
        "X-User-Id": identity.get("user_id", ""),
        "X-User-Name": identity.get("username", ""),
        "X-User-Role": identity.get("role", "user"),
    }
    if identity.get("kiosk_host_id"):
        extra_headers["X-Kiosk-Host-Id"] = identity["kiosk_host_id"]

    sid = path.split("/")[-1][:8]  # short session-id prefix for log correlation

    try:
        async with websockets.connect(
            upstream_url,
            extra_headers=extra_headers,
            # Use websockets library defaults for ping_interval/ping_timeout (20s/20s).
        ) as upstream:
            logger.info("ws upstream connected", extra={"session": sid, "upstream": upstream_url})

            async def from_client() -> None:
                try:
                    while True:
                        msg = await websocket.receive_text()
                        await upstream.send(msg)
                except WebSocketDisconnect:
                    logger.info("ws from_client: browser disconnected", extra={"session": sid})
                except Exception as exc:
                    logger.warning("ws from_client exit", extra={"session": sid,
                                   "error": type(exc).__name__, "detail": str(exc)[:120]})

            async def from_upstream() -> None:
                try:
                    async for msg in upstream:
                        if isinstance(msg, bytes):
                            await websocket.send_bytes(msg)
                        else:
                            await websocket.send_text(msg)
                except websockets.ConnectionClosed as exc:
                    logger.info("ws from_upstream: upstream closed",
                                extra={"session": sid, "code": exc.code, "reason": str(exc.reason)[:80]})
                except Exception as exc:
                    logger.warning("ws from_upstream exit", extra={"session": sid,
                                   "error": type(exc).__name__, "detail": str(exc)[:120]})

            client_task   = asyncio.create_task(from_client())
            upstream_task = asyncio.create_task(from_upstream())
            try:
                done, _ = await asyncio.wait(
                    {client_task, upstream_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                finished = next(iter(done))
                logger.info("ws pump: first task done",
                            extra={"session": sid,
                                   "task": "from_client" if finished is client_task else "from_upstream"})
            finally:
                for t in (client_task, upstream_task):
                    if not t.done():
                        t.cancel()
                        try:
                            await t
                        except (asyncio.CancelledError, Exception):
                            pass

    except (websockets.WebSocketException, OSError) as exc:
        logger.warning("ws upstream connection failed", extra={"session": sid,
                       "upstream": upstream_url, "error": str(exc)})

    try:
        await websocket.close(code=1000)
    except Exception:
        pass
