from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_action
from ..database import get_session
from ..deps import current_user_id, require_service_token
from ..models import ZAApiToken
from ..schemas import ApiTokenCreate, ApiTokenCreated, ApiTokenOut
from ..security import generate_pat, hash_pat, pat_prefix

router = APIRouter(prefix="/api-tokens", tags=["api-tokens"], dependencies=[Depends(require_service_token)])


@router.get("", response_model=list[ApiTokenOut])
async def list_tokens(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
):
    result = await session.execute(select(ZAApiToken).where(ZAApiToken.user_id == user_id))
    return result.scalars().all()


@router.post("", response_model=ApiTokenCreated, status_code=status.HTTP_201_CREATED)
async def create_token(
    payload: ApiTokenCreate,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    raw_token = generate_pat()
    token = ZAApiToken(
        user_id=user_id,
        name=payload.name,
        token_hash=hash_pat(raw_token),
        token_prefix=pat_prefix(raw_token),
        scopes=payload.scopes,
        expires_at=payload.expires_at,
    )
    session.add(token)
    await session.commit()
    await session.refresh(token)

    await log_action(
        session, user_id=user_id, username=actor_name or "", action="api_token.create",
        resource_type="api_token", resource_id=token.id, details={"name": token.name, "scopes": token.scopes},
    )

    return ApiTokenCreated(
        id=token.id,
        name=token.name,
        token_prefix=token.token_prefix,
        scopes=token.scopes,
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
        created_at=token.created_at,
        revoked_at=token.revoked_at,
        token=raw_token,
    )


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: str,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    result = await session.execute(select(ZAApiToken).where(ZAApiToken.id == token_id, ZAApiToken.user_id == user_id))
    token = result.scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="token not found")

    token.revoked_at = datetime.now(timezone.utc)
    await session.commit()

    await log_action(
        session, user_id=user_id, username=actor_name or "", action="api_token.revoke",
        resource_type="api_token", resource_id=token_id,
    )
