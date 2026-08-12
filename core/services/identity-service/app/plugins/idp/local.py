"""Local username/password identity provider — backed by za_users.password_hash."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.pluginbase import IdentityProvider

from ...models import ZARole, ZAUser
from ...security import verify_password


class LocalIdentityProvider(IdentityProvider):
    name = "local"

    async def authenticate(self, **credentials: Any) -> dict | None:
        session: AsyncSession = credentials["session"]
        username: str = credentials["username"]
        password: str = credentials["password"]

        result = await session.execute(select(ZAUser).where(ZAUser.username == username))
        user = result.scalar_one_or_none()
        if user is None or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None

        role_name = None
        if user.role_id:
            role_result = await session.execute(select(ZARole).where(ZARole.id == user.role_id))
            role = role_result.scalar_one_or_none()
            role_name = role.name if role else None

        return {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": role_name or "user",
        }
