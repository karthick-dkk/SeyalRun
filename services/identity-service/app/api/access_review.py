"""Access review MVP (PCI DSS 7.2.4 — periodic access review). A campaign
snapshots every currently-active ZAAuthorization; an admin walks each item to
keep/revoke, and a "revoke" decision feeds directly into the authorization's
existing Phase B lifecycle (status="revoked", enabled=False) — not a separate
disable path, and not a full GRC module (no role/group scope filtering, no
multi-reviewer workflow) — just enough for a real, auditable "reviewed on a
schedule" record.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_action
from ..database import get_session
from ..deps import require_admin, require_service_token
from ..models import ZAAccessReviewCampaign, ZAAccessReviewItem, ZAAuthorization
from ..schemas import AccessReviewCampaignCreate, AccessReviewCampaignOut, AccessReviewDecision, AccessReviewItemOut

router = APIRouter(prefix="/access-reviews", tags=["access-reviews"], dependencies=[Depends(require_service_token)])


async def _campaign_out(session: AsyncSession, campaign: ZAAccessReviewCampaign) -> AccessReviewCampaignOut:
    items = await session.execute(
        select(ZAAccessReviewItem).where(ZAAccessReviewItem.campaign_id == campaign.id)
    )
    rows = items.scalars().all()
    return AccessReviewCampaignOut(
        id=campaign.id, name=campaign.name, due_date=campaign.due_date, created_by=campaign.created_by,
        status=campaign.status, created_at=campaign.created_at, completed_at=campaign.completed_at,
        total_items=len(rows), pending_items=sum(1 for r in rows if r.decision == "pending"),
    )


@router.get("", response_model=list[AccessReviewCampaignOut], dependencies=[Depends(require_admin)])
async def list_campaigns(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAAccessReviewCampaign).order_by(ZAAccessReviewCampaign.created_at.desc()))
    return [await _campaign_out(session, c) for c in result.scalars().all()]


@router.post("", response_model=AccessReviewCampaignOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_campaign(
    payload: AccessReviewCampaignCreate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    campaign = ZAAccessReviewCampaign(name=payload.name, due_date=payload.due_date, created_by=actor_id)
    session.add(campaign)
    await session.flush()

    # Snapshot every currently-active authorization into a review item.
    active = await session.execute(select(ZAAuthorization).where(ZAAuthorization.enabled.is_(True)))
    for authz in active.scalars().all():
        session.add(ZAAccessReviewItem(
            campaign_id=campaign.id, authorization_id=authz.id, authorization_name=authz.name,
        ))

    await session.commit()
    await session.refresh(campaign)

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="access_review.create",
        resource_type="access_review_campaign", resource_id=campaign.id, details={"name": campaign.name},
    )
    return await _campaign_out(session, campaign)


@router.get("/{campaign_id}/items", response_model=list[AccessReviewItemOut], dependencies=[Depends(require_admin)])
async def list_items(campaign_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(ZAAccessReviewItem).where(ZAAccessReviewItem.campaign_id == campaign_id)
    )
    return result.scalars().all()


@router.post("/{campaign_id}/items/{item_id}/decide", response_model=AccessReviewItemOut, dependencies=[Depends(require_admin)])
async def decide_item(
    campaign_id: str,
    item_id: str,
    payload: AccessReviewDecision,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    if payload.decision not in ("keep", "revoke"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="decision must be 'keep' or 'revoke'")

    result = await session.execute(
        select(ZAAccessReviewItem).where(ZAAccessReviewItem.id == item_id, ZAAccessReviewItem.campaign_id == campaign_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="review item not found")

    item.decision = payload.decision
    item.reviewed_by = actor_id
    item.reviewed_at = datetime.now(timezone.utc)

    if payload.decision == "revoke":
        authz_result = await session.execute(select(ZAAuthorization).where(ZAAuthorization.id == item.authorization_id))
        authz = authz_result.scalar_one_or_none()
        if authz is not None:
            authz.enabled = False
            authz.status = "revoked"

    await session.commit()
    await session.refresh(item)

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="access_review.decide",
        resource_type="access_review_item", resource_id=item.id,
        details={"decision": payload.decision, "authorization_id": item.authorization_id}, result="success",
    )
    return item


@router.post("/{campaign_id}/complete", response_model=AccessReviewCampaignOut, dependencies=[Depends(require_admin)])
async def complete_campaign(
    campaign_id: str,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    campaign = await session.get(ZAAccessReviewCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="campaign not found")

    campaign.status = "completed"
    campaign.completed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(campaign)

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="access_review.complete",
        resource_type="access_review_campaign", resource_id=campaign.id, result="success",
    )
    return await _campaign_out(session, campaign)
