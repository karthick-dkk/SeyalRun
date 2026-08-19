from __future__ import annotations

from fastapi import Header, HTTPException, WebSocket, status
from libs.servicetoken import verify
from .config import get_settings


def require_service_token(x_service_token: str = Header(..., alias="X-Service-Token")) -> dict:
    settings = get_settings()
    try:
        return verify(x_service_token, "automation-service", settings.service_jwt_secret)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid service token")


async def require_ws_service_token(websocket: WebSocket) -> bool:
    """The same guard as require_service_token, for the app-level WebSocket routes.

    /ws/jobs/{run_id}/log and /ws/notifications are mounted on the app rather than
    on the service-token-guarded router, so their paths match what api-gateway's
    ws_proxy dials. Moving them off the router also moved them off its dependency,
    and for a while nothing authenticated those two upgrades at all: any workload
    that could reach this port could set X-User-Id and read another user's
    notification stream, or tail any run's live job log by run_id. Both carry
    command output, which is exactly where secrets surface.

    Closes on 4401 and returns False so callers can `if not await ...: return`,
    matching terminal-service's handle_terminal/handle_spectate.
    """
    settings = get_settings()
    try:
        verify(websocket.headers.get("x-service-token", ""), "automation-service", settings.service_jwt_secret)
    except Exception:
        await websocket.close(code=4401)
        return False
    return True


def get_user_id(x_user_id: str = Header(..., alias="X-User-Id")) -> str:
    return x_user_id


def get_user_role(x_user_role: str = Header(default="user", alias="X-User-Role")) -> str:
    return x_user_role


def get_user_real_role(x_user_real_role: str = Header(default="user", alias="X-User-Real-Role")) -> str:
    """The caller's true, un-elevated role — api-gateway's downstream_role() vouches any
    capability-authorized WRITE up to X-User-Role: admin so ordinary admin-gated CRUD
    guards work unmodified for custom roles (e.g. 'support', which has broad job-runs
    write capability). That means X-User-Role can't tell a real admin from a vouched
    support write. Use this instead of get_user_role() anywhere a check needs to
    distinguish "genuinely admin/superadmin" from "any role with write capability on
    this segment" — e.g. approval-gate role checks, where a template's approver_role
    is meant to restrict to admin-tier people specifically, not everyone who can POST
    job-runs. See services/inventory-service/app/api/zones.py for the same pattern."""
    return x_user_real_role
