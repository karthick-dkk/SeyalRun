"""Alerts API — rules CRUD, history, notification channels, test delivery."""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_async_session
from ..dependencies import get_current_user
from ..models.domain import AlertHistory, AlertRule, NotificationChannel
from ..models.schemas import (
    AlertHistoryResponse,
    AlertRuleCreate,
    AlertRuleListResponse,
    AlertRuleResponse,
    AlertRuleUpdate,
    ChannelCreate,
    ChannelResponse,
)

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


# ── helpers ──────────────────────────────────────────────────────────────────


def _rule_to_response(r: AlertRule) -> AlertRuleResponse:
    return AlertRuleResponse(
        id=r.id,
        name=r.name,
        description=r.description,
        enabled=r.enabled,
        event_type=r.event_type,
        conditions=r.conditions or {},
        channels=r.channels or [],
        created_by=r.created_by,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def _history_to_response(h: AlertHistory) -> AlertHistoryResponse:
    return AlertHistoryResponse(
        id=h.id,
        rule_id=h.rule_id,
        rule_name=h.rule_name,
        event_type=h.event_type,
        event_payload=h.event_payload or {},
        channels_tried=h.channels_tried or [],
        delivery_status=h.delivery_status,
        delivered_at=h.delivered_at,
        error_detail=h.error_detail,
    )


def _channel_to_response(c: NotificationChannel) -> ChannelResponse:
    return ChannelResponse(
        id=c.id,
        name=c.name,
        channel_type=c.channel_type,
        config=c.config or {},
        is_active=c.is_active,
        created_at=c.created_at,
    )


# ── Alert Rules ───────────────────────────────────────────────────────────────


@router.get("/rules", response_model=AlertRuleListResponse)
async def list_alert_rules(
    enabled: bool | None = Query(None),
    event_type: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> AlertRuleListResponse:
    q = select(AlertRule)
    if enabled is not None:
        q = q.where(AlertRule.enabled == enabled)
    if event_type:
        q = q.where(AlertRule.event_type == event_type)

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    q = q.order_by(AlertRule.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    rules = result.scalars().all()

    return AlertRuleListResponse(total=total, items=[_rule_to_response(r) for r in rules])


@router.post("/rules", response_model=AlertRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_alert_rule(
    body: AlertRuleCreate,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> AlertRuleResponse:
    r = AlertRule(
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        event_type=body.event_type,
        conditions=body.conditions.model_dump()
        if hasattr(body.conditions, "model_dump")
        else body.conditions,
        channels=[ch.model_dump() if hasattr(ch, "model_dump") else ch for ch in body.channels],
        created_by=user["username"],
    )
    db.add(r)
    await db.flush()
    log.info("alert_rule_created", id=str(r.id), name=r.name, user=user["username"])
    return _rule_to_response(r)


@router.get("/rules/{rule_id}", response_model=AlertRuleResponse)
async def get_alert_rule(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> AlertRuleResponse:
    r = await db.get(AlertRule, rule_id)
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert rule not found")
    return _rule_to_response(r)


@router.put("/rules/{rule_id}", response_model=AlertRuleResponse)
async def update_alert_rule(
    rule_id: uuid.UUID,
    body: AlertRuleCreate,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> AlertRuleResponse:
    r = await db.get(AlertRule, rule_id)
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert rule not found")

    r.name = body.name
    r.description = body.description
    r.enabled = body.enabled
    r.event_type = body.event_type
    r.conditions = (
        body.conditions.model_dump() if hasattr(body.conditions, "model_dump") else body.conditions
    )
    r.channels = [ch.model_dump() if hasattr(ch, "model_dump") else ch for ch in body.channels]

    await db.flush()
    return _rule_to_response(r)


@router.patch("/rules/{rule_id}", response_model=AlertRuleResponse)
async def patch_alert_rule(
    rule_id: uuid.UUID,
    body: AlertRuleUpdate,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> AlertRuleResponse:
    r = await db.get(AlertRule, rule_id)
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert rule not found")

    if body.name is not None:
        r.name = body.name
    if body.description is not None:
        r.description = body.description
    if body.enabled is not None:
        r.enabled = body.enabled
    if body.conditions is not None:
        r.conditions = (
            body.conditions.model_dump()
            if hasattr(body.conditions, "model_dump")
            else body.conditions
        )
    if body.channels is not None:
        r.channels = [ch.model_dump() if hasattr(ch, "model_dump") else ch for ch in body.channels]

    await db.flush()
    return _rule_to_response(r)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert_rule(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> None:
    r = await db.get(AlertRule, rule_id)
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert rule not found")
    await db.delete(r)


@router.post("/rules/{rule_id}/test")
async def test_alert_rule(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> dict:
    """Fire a test alert for this rule immediately."""
    r = await db.get(AlertRule, rule_id)
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert rule not found")

    from ..services.notifier import fire_test_alert

    result = await fire_test_alert(r, triggered_by=user["username"])

    log.info("test_alert_fired", rule_id=str(rule_id), user=user["username"])
    return {"rule_id": str(rule_id), "test_result": result}


# ── Alert History ─────────────────────────────────────────────────────────────


@router.get("/history")
async def list_alert_history(
    rule_id: uuid.UUID | None = Query(None),
    delivery_status: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> dict:
    q = select(AlertHistory)
    if rule_id:
        q = q.where(AlertHistory.rule_id == rule_id)
    if delivery_status:
        q = q.where(AlertHistory.delivery_status == delivery_status)

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    q = q.order_by(AlertHistory.delivered_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    history = result.scalars().all()

    return {
        "total": total,
        "items": [_history_to_response(h).model_dump() for h in history],
    }


# ── Notification Channels ─────────────────────────────────────────────────────


@router.get("/channels")
async def list_channels(
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> dict:
    result = await db.execute(
        select(NotificationChannel).order_by(NotificationChannel.created_at.desc())
    )
    channels = result.scalars().all()
    return {
        "total": len(channels),
        "items": [_channel_to_response(c).model_dump() for c in channels],
    }


@router.post("/channels", response_model=ChannelResponse, status_code=status.HTTP_201_CREATED)
async def create_channel(
    body: ChannelCreate,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> ChannelResponse:
    c = NotificationChannel(
        name=body.name,
        channel_type=body.channel_type,
        config=body.config,
        is_active=body.is_active,
    )
    db.add(c)
    await db.flush()
    log.info("channel_created", id=str(c.id), name=c.name, type=c.channel_type)
    return _channel_to_response(c)


@router.get("/channels/{channel_id}", response_model=ChannelResponse)
async def get_channel(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> ChannelResponse:
    c = await db.get(NotificationChannel, channel_id)
    if not c:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    return _channel_to_response(c)


@router.put("/channels/{channel_id}", response_model=ChannelResponse)
async def update_channel(
    channel_id: uuid.UUID,
    body: ChannelCreate,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> ChannelResponse:
    c = await db.get(NotificationChannel, channel_id)
    if not c:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")

    c.name = body.name
    c.channel_type = body.channel_type
    c.config = body.config
    c.is_active = body.is_active

    await db.flush()
    return _channel_to_response(c)


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> None:
    c = await db.get(NotificationChannel, channel_id)
    if not c:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    await db.delete(c)


@router.post("/channels/{channel_id}/test")
async def test_channel(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> dict:
    """Send a test notification through this channel."""
    c = await db.get(NotificationChannel, channel_id)
    if not c:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")

    from ..services.notifier import test_channel_delivery

    result = await test_channel_delivery(c, triggered_by=user["username"])

    return {"channel_id": str(channel_id), "result": result}
