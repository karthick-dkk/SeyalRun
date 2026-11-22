"""Request identity for posture-service.

Every route is reached only through api-gateway, which has already applied the
role matrix (rbaccore: admin all methods, support GET) and, for a PAT, the
token's scopes (apiscopes: posture:read for reads). What is enforced again here
is what the gateway cannot know: which hosts this caller may see, and that a
state change comes from a genuine admin rather than a write the gateway
"vouched" up to X-User-Role: admin for a custom role.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status
from libs.servicetoken import ServiceTokenError, verify

from .config import get_settings

# Mirrors api-gateway rbac._SEE_ALL_HOSTS: built-in broad roles see the whole
# host list; anyone else is narrowed to the hosts their authorizations name.
SEE_ALL_HOSTS = frozenset({"superadmin", "admin", "support"})
ADMIN_ROLES = frozenset({"superadmin", "admin"})


def require_service_token(x_service_token: str | None = Header(default=None, alias="X-Service-Token")) -> dict:
    if not x_service_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing X-Service-Token")
    try:
        return verify(x_service_token, "posture-service", get_settings().service_jwt_secret)
    except ServiceTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid service token") from exc


@dataclass(frozen=True)
class Caller:
    user_id: str
    username: str
    role: str        # as forwarded (a write may be vouched up to "admin")
    real_role: str   # the caller's true primary role
    kiosk_host_id: str | None
    ip: str

    @property
    def is_admin(self) -> bool:
        return self.real_role in ADMIN_ROLES

    @property
    def sees_all_hosts(self) -> bool:
        return self.kiosk_host_id is None and self.real_role in SEE_ALL_HOSTS


def get_caller(
    request: Request,
    _svc: dict = Depends(require_service_token),
    x_user_id: str = Header(default="", alias="X-User-Id"),
    x_user_name: str = Header(default="", alias="X-User-Name"),
    x_user_role: str = Header(default="", alias="X-User-Role"),
    x_user_real_role: str = Header(default="", alias="X-User-Real-Role"),
    x_kiosk_host_id: str = Header(default="", alias="X-Kiosk-Host-Id"),
    x_real_ip: str = Header(default="", alias="X-Real-IP"),
) -> Caller:
    if not x_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing X-User-Id")
    return Caller(
        user_id=x_user_id, username=x_user_name, role=x_user_role,
        real_role=x_user_real_role or x_user_role, kiosk_host_id=x_kiosk_host_id or None,
        ip=x_real_ip or (request.client.host if request.client else ""),
    )


def require_admin(caller: Caller = Depends(get_caller)) -> Caller:
    """Collect, import and activate are admin-only. Checked on the REAL role:
    X-User-Role is vouched up to admin for any write the gateway authorized, so
    it cannot tell an admin from a custom role that was granted posture POST."""
    if not caller.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin role required")
    return caller
