from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from libs.servicetoken import ServiceTokenError, verify

from .config import get_settings


def require_service_token(x_service_token: str = Header(default="")) -> None:
    settings = get_settings()
    try:
        verify(x_service_token, "recording-service", settings.service_jwt_secret)
    except (ServiceTokenError, Exception):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid service token")


def current_user_id(x_user_id: str = Header(default="")) -> str:
    if not x_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing user id")
    return x_user_id


def current_user_role(x_user_role: str = Header(default="user")) -> str:
    return x_user_role
