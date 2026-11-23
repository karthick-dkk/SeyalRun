"""GET /api/v1/auth/my-access — the CALLER's effective access rules.

Self-service and self-scoped: the user id comes only from the gateway-set
X-User-Id header (the authenticated session), never from the query or body, so
there is no way to ask for anyone else's rules. Lives under /auth because that
segment is reachable by every signed-in role (api-gateway rbac._ALWAYS) — the
plain "user" role cannot GET /authorizations (the admin list of every rule), and
should not be able to. PAT callers are refused at the gateway: "auth" maps to no
token scope (libs/apiscopes), so agents cannot enumerate their owner's grants.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import current_user_id, require_service_token
from ..models import ZAAuthorization, ZAUserGroup, ZAUserGroupMember
from ..myaccess import effective_access_rows

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(require_service_token)])


@router.get("/my-access")
async def my_access(
    user_id: str = Depends(current_user_id),
    session: AsyncSession = Depends(get_session),
):
    groups = {
        gid: name for gid, name in (
            await session.execute(
                select(ZAUserGroup.id, ZAUserGroup.name)
                .join(ZAUserGroupMember, ZAUserGroupMember.group_id == ZAUserGroup.id)
                .where(ZAUserGroupMember.user_id == user_id)
            )
        ).all()
    }
    authzs = (await session.execute(select(ZAAuthorization))).scalars().all()
    return {"items": effective_access_rows(authzs, user_id, groups, datetime.now(timezone.utc))}
