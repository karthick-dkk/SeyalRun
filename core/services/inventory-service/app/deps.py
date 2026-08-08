from __future__ import annotations

from fastapi import Header, HTTPException, status

from libs.servicetoken import ServiceTokenError, verify

from .config import get_settings


async def require_service_token(x_service_token: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if not x_service_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing X-Service-Token")
    try:
        verify(x_service_token, "inventory-service", settings.service_jwt_secret)
    except ServiceTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid service token: {exc}") from exc


async def current_user_id(x_user_id: str | None = Header(default=None)) -> str:
    if not x_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing X-User-Id")
    return x_user_id


async def require_admin(role: str = Header(default="user", alias="X-User-Role")) -> None:
    if role not in ("admin", "superadmin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin role required")
