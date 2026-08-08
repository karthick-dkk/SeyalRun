from __future__ import annotations

from fastapi import Header, HTTPException, status
from libs.servicetoken import verify
from .config import get_settings


def require_service_token(x_service_token: str = Header(..., alias="X-Service-Token")) -> dict:
    settings = get_settings()
    try:
        return verify(x_service_token, "zabbix-integration-service", settings.service_jwt_secret)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid service token")


def get_user_id(x_user_id: str = Header(..., alias="X-User-Id")) -> str:
    return x_user_id


def get_user_role(x_user_role: str = Header(default="user", alias="X-User-Role")) -> str:
    return x_user_role
