from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import require_admin, require_service_token
from ..models import ZAGateway, ZAZone
from ..schemas import GatewayCreate, GatewayOut, ZoneCreate, ZoneOut

router = APIRouter(tags=["zones"], dependencies=[Depends(require_service_token)])


async def _validate_parent_zone(session: AsyncSession, zone_id: str | None, parent_zone_id: str | None) -> None:
    """Zone nesting forms the ProxyJump chain (root ancestor connects first, this
    zone's own gateway is the last hop before the target host) — a cycle would make
    chain resolution loop forever, so it's rejected here rather than at connect time."""
    if not parent_zone_id:
        return
    if parent_zone_id == zone_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="zone cannot be its own parent")
    result = await session.execute(select(ZAZone.id).where(ZAZone.id == parent_zone_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="parent zone not found")
    current_id = parent_zone_id
    seen: set[str] = set()
    while current_id and current_id not in seen:
        if current_id == zone_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="parent assignment would create a zone cycle")
        seen.add(current_id)
        row = await session.execute(select(ZAZone.parent_zone_id).where(ZAZone.id == current_id))
        current_id = (row.first() or (None,))[0]


@router.get("/zones", response_model=list[ZoneOut])
async def list_zones(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAZone))
    return result.scalars().all()


@router.post("/zones", response_model=ZoneOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_zone(payload: ZoneCreate, session: AsyncSession = Depends(get_session)):
    existing = await session.execute(select(ZAZone).where(ZAZone.name == payload.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="zone name already exists")
    await _validate_parent_zone(session, None, payload.parent_zone_id)
    zone = ZAZone(**payload.model_dump())
    session.add(zone)
    await session.commit()
    await session.refresh(zone)
    return zone


@router.put("/zones/{zone_id}", response_model=ZoneOut, dependencies=[Depends(require_admin)])
async def update_zone(zone_id: str, payload: ZoneCreate, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAZone).where(ZAZone.id == zone_id))
    zone = result.scalar_one_or_none()
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="zone not found")
    await _validate_parent_zone(session, zone_id, payload.parent_zone_id)
    for field, value in payload.model_dump().items():
        setattr(zone, field, value)
    await session.commit()
    await session.refresh(zone)
    return zone


@router.get("/internal/zones/{zone_id}/gateway-chain")
async def zone_gateway_chain(zone_id: str, session: AsyncSession = Depends(get_session)):
    """Ordered ProxyJump hop list for connecting to a host in this zone — walks the
    zone's parent_zone_id ancestry from the outermost ancestor down to this zone
    (each zone contributes at most one hop, its first-created gateway), so nesting
    zones is how a multi-hop chain is built. Called by terminal-service (and,
    eventually, automation-service) instead of a single explicit gateway_id."""
    zones: list[ZAZone] = []
    seen: set[str] = set()
    current_id: str | None = zone_id
    while current_id and current_id not in seen:
        seen.add(current_id)
        result = await session.execute(select(ZAZone).where(ZAZone.id == current_id))
        zone = result.scalar_one_or_none()
        if zone is None:
            break
        zones.append(zone)
        current_id = zone.parent_zone_id
    zones.reverse()  # root ancestor first, target zone last

    hops = []
    for z in zones:
        gw_result = await session.execute(
            select(ZAGateway).where(ZAGateway.zone_id == z.id).order_by(ZAGateway.created_at).limit(1)
        )
        gw = gw_result.scalar_one_or_none()
        if gw is not None:
            hops.append({
                "id": gw.id,
                "zone_id": z.id,
                "zone_name": z.name,
                "host": gw.host,
                "port": gw.port,
                "username": gw.username,
                "credential_id": gw.credential_id,
            })
    return {"chain": hops}


@router.delete("/zones/{zone_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
async def delete_zone(zone_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAZone).where(ZAZone.id == zone_id))
    zone = result.scalar_one_or_none()
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="zone not found")
    await session.delete(zone)
    await session.commit()


@router.get("/internal/gateways/{gateway_id}", response_model=GatewayOut)
async def get_gateway_internal(gateway_id: str, session: AsyncSession = Depends(get_session)):
    """Direct gateway lookup by ID — called by terminal-service WS handler."""
    result = await session.execute(select(ZAGateway).where(ZAGateway.id == gateway_id))
    gw = result.scalar_one_or_none()
    if gw is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="gateway not found")
    return gw


@router.get("/zones/{zone_id}/gateways", response_model=list[GatewayOut])
async def list_gateways(zone_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAGateway).where(ZAGateway.zone_id == zone_id))
    return result.scalars().all()


@router.post("/zones/{zone_id}/gateways", response_model=GatewayOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_gateway(zone_id: str, payload: GatewayCreate, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAZone).where(ZAZone.id == zone_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="zone not found")
    data = payload.model_dump()
    data["zone_id"] = zone_id
    gateway = ZAGateway(**data)
    session.add(gateway)
    await session.commit()
    await session.refresh(gateway)
    return gateway


@router.put("/zones/{zone_id}/gateways/{gateway_id}", response_model=GatewayOut, dependencies=[Depends(require_admin)])
async def update_gateway(zone_id: str, gateway_id: str, payload: GatewayCreate, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAGateway).where(ZAGateway.id == gateway_id, ZAGateway.zone_id == zone_id))
    gateway = result.scalar_one_or_none()
    if gateway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="gateway not found")
    data = payload.model_dump()
    data["zone_id"] = zone_id
    for field, value in data.items():
        setattr(gateway, field, value)
    await session.commit()
    await session.refresh(gateway)
    return gateway


@router.delete("/zones/{zone_id}/gateways/{gateway_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
async def delete_gateway(zone_id: str, gateway_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAGateway).where(ZAGateway.id == gateway_id, ZAGateway.zone_id == zone_id))
    gateway = result.scalar_one_or_none()
    if gateway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="gateway not found")
    await session.delete(gateway)
    await session.commit()


@router.post("/zones/{zone_id}/gateways/{gateway_id}/test", dependencies=[Depends(require_admin)])
async def test_gateway(zone_id: str, gateway_id: str, session: AsyncSession = Depends(get_session)):
    """TCP-connect to the gateway's SSH port to verify reachability/health (stateless)."""
    result = await session.execute(select(ZAGateway).where(ZAGateway.id == gateway_id, ZAGateway.zone_id == zone_id))
    gateway = result.scalar_one_or_none()
    if gateway is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="gateway not found")

    reachable = False
    error_msg = ""
    start = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(gateway.host, gateway.port or 22), timeout=5.0
        )
        writer.close()
        await writer.wait_closed()
        reachable = True
    except asyncio.TimeoutError:
        error_msg = "Connection timed out"
    except OSError as exc:
        error_msg = str(exc)
    latency_ms = int((time.monotonic() - start) * 1000)

    return {"gateway_id": gateway_id, "reachable": reachable, "latency_ms": latency_ms, "error": error_msg}
