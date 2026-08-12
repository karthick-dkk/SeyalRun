"""Idempotent seed: ensures a superadmin user exists.

Run via ``python -m app.seed`` after migrations. The account is always
created with the shipped default password ("seyalrun") and
``must_change_password`` set — the api-gateway locks the session to the
change-password endpoint until it is rotated, so the default can never be
used to operate the console. There is no way to seed a custom password;
rotation at first login is the only path.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from ._pwpolicy import DEFAULT_SEED_PASSWORD

from .config import get_settings
from .database import SessionLocal
from .models import ZARole, ZAUser, ZAUserRole
from .security import hash_password


async def main() -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        existing = await session.execute(select(ZAUser).where(ZAUser.username == settings.seed_admin_username))
        if existing.scalar_one_or_none():
            print(f"seed: user '{settings.seed_admin_username}' already exists — skipping")
            return

        role_result = await session.execute(select(ZARole).where(ZARole.name == "superadmin"))
        role = role_result.scalar_one_or_none()
        if role is None:
            raise RuntimeError("za_roles.superadmin not found — run schema import / migrations first")

        user = ZAUser(
            username=settings.seed_admin_username,
            display_name="SeyalRun Administrator",
            role_id=role.id,
            password_hash=hash_password(DEFAULT_SEED_PASSWORD),
            is_active=True,
            # Default credentials are public knowledge — force rotation at first login.
            must_change_password=True,
        )
        session.add(user)
        await session.flush()
        # v2: za_user_roles is authoritative for effective_roles. Without this row the
        # seeded admin resolves to no roles and is locked out. role_id is kept only as a
        # legacy display mirror.
        session.add(ZAUserRole(user_id=user.id, role_id=role.id))
        await session.commit()

        print(f"seed: created superadmin user '{settings.seed_admin_username}'")
        print(
            f"seed: default password is '{DEFAULT_SEED_PASSWORD}' — "
            "a new password is REQUIRED at first login before anything else works"
        )


if __name__ == "__main__":
    asyncio.run(main())
