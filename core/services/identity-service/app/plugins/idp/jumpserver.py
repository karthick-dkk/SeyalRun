"""JumpServer delegated identity provider.

Validates a JumpServer access token against JumpServer's own API and, on success,
auto-provisions the user in SeyalRun (the same shape as the Zabbix SSO provider).
This is how a SeyalRun deployment that fronts an existing JumpServer lets those
users in without a second password: JumpServer stays the source of truth for who
they are; SeyalRun maps them onto its RBAC roles.

Disabled unless ``jumpserver_api_url`` is set — with it blank ``authenticate``
returns None and login falls through to the other providers. TLS verification is
always on; ``jumpserver_ca_bundle`` only supplies the trust root for a self-signed
JumpServer (it is never a way to turn verification off).

Role mapping: a JumpServer superuser -> ``superadmin``; an org/system admin ->
``admin``; everyone else -> ``user``.
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.pluginbase import IdentityProvider

from ...config import get_settings
from ...models import ZARole, ZAUser, ZAUserRole


def role_for_profile(profile: dict) -> str:
    """Map a JumpServer user profile onto a SeyalRun role name. Pure — the whole
    privilege decision lives here so it can be tested without a JumpServer."""
    if profile.get("is_superuser"):
        return "superadmin"
    roles = profile.get("system_roles") or profile.get("roles") or []
    names = " ".join(
        str(r.get("name", "") if isinstance(r, dict) else r) for r in roles
    ).lower()
    if profile.get("is_org_admin") or "admin" in names:
        return "admin"
    return "user"


class JumpServerProvider(IdentityProvider):
    name = "jumpserver"

    async def _fetch_profile(self, token: str) -> dict | None:
        """Ask JumpServer who this token belongs to. None if disabled, unreachable,
        or the token is not valid."""
        settings = get_settings()
        base = (settings.jumpserver_api_url or "").rstrip("/")
        if not base or not token:
            return None
        verify: Any = settings.jumpserver_ca_bundle or True  # trust root, verification stays on
        try:
            async with httpx.AsyncClient(base_url=base, timeout=8.0, verify=verify) as client:
                resp = await client.get(
                    "/api/v1/users/profile/",
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        return data if data.get("username") else None

    async def authenticate(self, **credentials: Any) -> dict | None:
        session: AsyncSession = credentials["session"]
        token: str | None = credentials.get("jms_token")
        if not token:
            return None

        profile = await self._fetch_profile(token)
        if not profile:
            return None

        username = profile["username"]
        role_name = role_for_profile(profile)

        user = (await session.execute(
            select(ZAUser).where(ZAUser.username == username)
        )).scalar_one_or_none()
        role = (await session.execute(
            select(ZARole).where(ZARole.name == role_name)
        )).scalar_one_or_none()

        is_new = user is None
        if is_new:
            user = ZAUser(
                username=username,
                display_name=profile.get("name") or username,
                is_active=True,
                role_id=role.id if role else None,  # legacy display fallback only
            )
            session.add(user)
            await session.flush()
        elif role and user.role_id != role.id:
            user.role_id = role.id

        # RBAC enforcement (api-gateway) reads za_user_roles, not the legacy role_id —
        # keep the effective role in sync on every login, exactly as zabbix_sso does,
        # or an auto-provisioned user shows the right role but has zero permissions.
        if role:
            links = (await session.execute(
                select(ZAUserRole).where(ZAUserRole.user_id == user.id)
            )).scalars().all()
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
