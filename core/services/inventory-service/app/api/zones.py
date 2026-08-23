from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import require_admin, require_service_token
from ..models import ZACredentialHostLink, ZAGateway, ZAHost, ZAZone
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
    """Ordered ProxyJump hop list for connecting to a host in this zone.

    Walks the zone's parent_zone_id ancestry from the outermost ancestor down to
    this zone, so nesting zones is how a multi-hop chain is built. Each zone
    contributes EVERY enabled gateway it holds, in gateway_order — a zone may have
    a redundant pair, or a chain through a DMZ. (It previously took one per zone,
    so a zone's second gateway was silently ignored.)

    A gateway is a host with host_type "gateway", carrying its own credential like
    any other host. za_gateways is the older shape and is still read when a zone
    has no gateway hosts, so a deployment that has not been reviewed keeps
    connecting exactly as before.

    Called by terminal-service instead of a single explicit gateway_id.
    """
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
    # A zone CYCLE is already impossible (_validate_parent_zone rejects one at
    # write time, and the ancestry walk above carries its own `seen` guard). What
    # was not guarded is the same HOST appearing twice in the resolved chain:
    # distinct zones may legitimately exist while both name the same gateway
    # machine, and nesting them produced "ssh -J gw,gw" — a second hop from a host
    # to itself, which stalls the connection rather than failing it. Deduplicated
    # by endpoint, keeping the OUTERMOST occurrence, because that is the one
    # reachable from where the chain starts.
    seen_endpoints: set[tuple[str, int]] = set()
    skipped: list[str] = []
    for z in zones:
        # Gateway HOSTS first. A gateway is an ordinary host marked host_type
        # "gateway", so it carries groups, a zone and its own credential like any
        # other; za_gateways is the old shape, kept as a fallback so a deployment
        # whose gateways have not been reviewed keeps connecting exactly as before.
        #
        # A zone may hold SEVERAL gateways — a redundant pair, or a chain through a
        # DMZ — so every one contributes a hop, in gateway_order. Ordering is
        # explicit rather than by created_at: which gateway comes first is a
        # topology decision, not an accident of when someone typed it in.
        gw_hosts = (await session.execute(
            select(ZAHost)
            .where(ZAHost.zone_id == z.id, ZAHost.host_type == "gateway", ZAHost.enabled.is_(True))
            .order_by(ZAHost.gateway_order, ZAHost.created_at)
        )).scalars().all()

        candidates: list[dict] = []
        for h in gw_hosts:
            # A gateway host logs in with its OWN linked credential, exactly like
            # any other host — that is the whole benefit of modelling it as one.
            # Without resolving it here the hop would carry no login and the jump
            # would fail at connect time with nothing explaining why.
            link = (await session.execute(
                select(ZACredentialHostLink.credential_id)
                .where(ZACredentialHostLink.host_id == h.id)
                .limit(1)
            )).scalar_one_or_none()
            candidates.append({
                "id": h.id, "host": h.ip, "port": h.port, "username": "",
                "credential_id": link, "host_id": h.id,
            })
        if not candidates:
            legacy = (await session.execute(
                select(ZAGateway).where(ZAGateway.zone_id == z.id).order_by(ZAGateway.created_at)
            )).scalars().all()
            candidates = [
                {"id": g.id, "host": g.host, "port": g.port, "username": g.username,
                 "credential_id": g.credential_id, "host_id": None}
                for g in legacy
            ]

        for cand in candidates:
            endpoint = ((cand["host"] or "").strip().lower(), int(cand["port"] or 22))
            if endpoint in seen_endpoints:
                skipped.append(f"{z.name} ({cand['host']})")
                continue
            seen_endpoints.add(endpoint)
            hops.append({**cand, "zone_id": z.id, "zone_name": z.name})
    # Reported rather than silently dropped: a duplicated gateway usually means the
    # zone tree is misconfigured, and a chain that quietly works while the topology
    # is wrong is how it stays wrong.
    return {"chain": hops, "skipped_duplicate_gateways": skipped}


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
