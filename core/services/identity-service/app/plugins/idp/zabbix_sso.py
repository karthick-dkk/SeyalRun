"""Zabbix SSO identity provider.

Mirrors the v1 flow (zabbix-module-seyalrun): the Zabbix module calls
``/auth/sso-init`` from 127.0.0.1 with the logged-in Zabbix user's username
and numeric ``USER_TYPE_*``. This returns a one-time, 120-second opaque
code embedded in the iframe URL. The frontend then calls
``/auth/sso-exchange`` with that code to receive a SeyalRun session JWT.

Users are auto-provisioned on first SSO login: ``zabbix_user_type``
1=user, 2=admin, 3=superadmin maps to the matching ``za_roles.name``.

SSO codes are held in-process (single-replica assumption documented in
README; Phase 2 may move this to Redis if identity-service is scaled out).
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.pluginbase import IdentityProvider

from ...models import ZARole, ZAUser, ZAUserRole

_CODE_TTL_SECONDS = 120
_codes: dict[str, dict] = {}

_ROLE_BY_USER_TYPE = {1: "user", 2: "admin", 3: "superadmin"}


def _purge_expired() -> None:
    now = time.time()
    expired = [c for c, v in _codes.items() if v["expires_at"] < now]
    for c in expired:
        _codes.pop(c, None)


class ZabbixSSOProvider(IdentityProvider):
    name = "zabbix_sso"

    def init_sso(self, username: str, zabbix_user_type: int) -> str:
        _purge_expired()
        code = secrets.token_urlsafe(24)
        _codes[code] = {
            "username": username,
            "zabbix_user_type": zabbix_user_type,
            "expires_at": time.time() + _CODE_TTL_SECONDS,
        }
        return code

    async def authenticate(self, **credentials: Any) -> dict | None:
        session: AsyncSession = credentials["session"]
        sso_code: str = credentials["sso_code"]

        _purge_expired()
        entry = _codes.pop(sso_code, None)
        if entry is None:
            return None

        username = entry["username"]
        zabbix_user_type = entry["zabbix_user_type"]
        role_name = _ROLE_BY_USER_TYPE.get(zabbix_user_type, "user")

        result = await session.execute(select(ZAUser).where(ZAUser.username == username))
        user = result.scalar_one_or_none()

        role_result = await session.execute(select(ZARole).where(ZARole.name == role_name))
        role = role_result.scalar_one_or_none()

        is_new = user is None
        if is_new:
            user = ZAUser(
                username=username,
                display_name=username,
                zabbix_userid=username,
                role_id=role.id if role else None,  # legacy column — kept for display fallback only
                is_active=True,
            )
            session.add(user)
            await session.flush()
        elif role and user.role_id != role.id:
            user.role_id = role.id

        # The v3 RBAC system (api-gateway's actual enforcement) resolves permissions from
        # za_user_roles, NOT the legacy role_id column above — effective_roles() never reads
        # it. Without this, an auto-provisioned Zabbix SSO user shows the right role_name in
        # the login response but has ZERO effective permissions (every request 403s). Keep
        # the direct role assignment in sync with Zabbix's current role on every login, the
        # same way the admin Users page's _set_user_roles does for a manual role change.
        if role:
            existing = await session.execute(select(ZAUserRole).where(ZAUserRole.user_id == user.id))
            links = existing.scalars().all()
            if is_new or not any(link.role_id == role.id for link in links):
                for link in links:
                    await session.delete(link)
                session.add(ZAUserRole(user_id=user.id, role_id=role.id))

        await session.commit()

        return {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": role_name,
        }
