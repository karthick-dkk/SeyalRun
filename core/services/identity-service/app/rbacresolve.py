"""The ONE effective-role resolver — every auth path routes through this.

effective_roles(session, user_id) = direct za_user_roles ∪ roles granted by every
group the user belongs to (za_user_group_members ⋈ za_group_roles). Two queries, no
N+1; the pure union/dedup lives in libs.rbaccore.merge_roles so it is unit-tested
without a DB. JWT mint, the gateway live-refresh endpoint, PAT verify, and SSO all
call this — so group-granted access can never appear or vanish by auth path.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.rbaccore import authz_bypass_role_names, merge_roles

from .models import ZAGroupRole, ZARole, ZAUserGroupMember, ZAUserRole


async def effective_roles(session: AsyncSession, user_id: str) -> list[str]:
    # Query 1: roles assigned directly to the user.
    direct = [
        n for (n,) in (
            await session.execute(
                select(ZARole.name)
                .join(ZAUserRole, ZARole.id == ZAUserRole.role_id)
                .where(ZAUserRole.user_id == user_id)
            )
        ).all()
    ]
    # Query 2: roles granted by the user's groups (single join, not N+1 per group).
    group_roles = [
        n for (n,) in (
            await session.execute(
                select(ZARole.name)
                .join(ZAGroupRole, ZARole.id == ZAGroupRole.role_id)
                .join(ZAUserGroupMember, ZAUserGroupMember.group_id == ZAGroupRole.group_id)
                .where(ZAUserGroupMember.user_id == user_id)
            )
        ).all()
    ]
    return merge_roles(direct, group_roles)


async def users_with_bypass_role(session: AsyncSession) -> set[str]:
    """Bulk equivalent of calling bypasses_authz(await effective_roles(session,
    uid)) for every user — two queries total instead of 1+2N, for callers that
    need "which of these users hold admin/superadmin" rather than one user's
    full role list (e.g. resolving who's eligible to approve an authorization
    request, services/identity-service/app/api/authorizations.py)."""
    bypass_roles = authz_bypass_role_names()
    direct = await session.execute(
        select(ZAUserRole.user_id)
        .join(ZARole, ZARole.id == ZAUserRole.role_id)
        .where(ZARole.name.in_(bypass_roles))
    )
    via_group = await session.execute(
        select(ZAUserGroupMember.user_id)
        .join(ZAGroupRole, ZAGroupRole.group_id == ZAUserGroupMember.group_id)
        .join(ZARole, ZARole.id == ZAGroupRole.role_id)
        .where(ZARole.name.in_(bypass_roles))
    )
    return {uid for (uid,) in direct.all()} | {uid for (uid,) in via_group.all()}
