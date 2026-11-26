"""Zabbix API calls posture-service needs around a patch rollout (3.0 M4).

Internal only (service token). Maintenance is opened "with data collection" so
items keep flowing and the post-patch checks can see real problems; only alerting
is suppressed. SeyalRun ends only maintenances it created itself.
"""

from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from libs.servicetoken import mint
from pydantic import BaseModel, Field

from app.api.webhook import _resolve_zabbix_creds
from app.config import get_settings
from app.deps import require_service_token

router = APIRouter(dependencies=[Depends(require_service_token)])
NAME_PREFIX = "SeyalRun patch "


async def _rpc(method: str, params: dict):
    url, token = await _resolve_zabbix_creds(get_settings())
    if not url or not token:
        raise HTTPException(status_code=424, detail="Zabbix API is not configured")
    base = url.rstrip("/")
    if not base.endswith("/api_jsonrpc.php"):
        base += "/api_jsonrpc.php"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(base, json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
                                  headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Zabbix API call failed ({type(exc).__name__})") from exc
    if "error" in data:
        err = data["error"] or {}
        raise HTTPException(status_code=502, detail=f"Zabbix {method}: {str(err.get('data') or err.get('message'))[:200]}")
    return data.get("result")


async def _zabbix_hostid(host_id: str) -> str:
    s = get_settings()
    tok = mint("zabbix-integration-service", "inventory-service", s.service_jwt_secret)
    try:
        async with httpx.AsyncClient(base_url=s.inventory_service_url, timeout=10.0) as client:
            r = await client.get(f"/api/v1/internal/hosts/{host_id}", headers={"X-Service-Token": tok})
        r.raise_for_status()
        hid = r.json().get("zabbix_hostid")
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="inventory lookup failed") from exc
    if not hid:
        raise HTTPException(status_code=424, detail="host is not linked to a Zabbix host")
    return str(hid)


class MaintenanceCreate(BaseModel):
    host_id: str = Field(min_length=1, max_length=36)
    reference: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9-]+$")
    minutes: int = Field(ge=5, le=1440)
    description: str = Field(default="", max_length=500)


@router.post("/internal/zabbix/maintenance", status_code=201)
async def create_maintenance(payload: MaintenanceCreate):
    """Idempotent by reference: asking again extends the existing one."""
    hostid = await _zabbix_hostid(payload.host_id)
    name = f"{NAME_PREFIX}{payload.reference}"
    found = await _rpc("maintenance.get", {"output": ["maintenanceid"], "filter": {"name": [name]}})
    now = int(time.time()) // 60 * 60       # Zabbix rounds to minutes
    secs = payload.minutes * 60
    if found:
        mid = found[0]["maintenanceid"]
        await _rpc("maintenance.update", {
            "maintenanceid": mid, "active_since": now, "active_till": now + secs,
            "timeperiods": [{"timeperiod_type": 0, "start_date": now, "period": secs}]})
        return {"maintenance_id": mid, "reused": True}
    res = await _rpc("maintenance.create", {
        "name": name, "active_since": now, "active_till": now + secs, "maintenance_type": 0,
        "description": payload.description or "Opened by a SeyalRun patch rollout",
        "hosts": [{"hostid": hostid}],
        "timeperiods": [{"timeperiod_type": 0, "start_date": now, "period": secs}],
    })
    return {"maintenance_id": res["maintenanceids"][0], "reused": False}


@router.delete("/internal/zabbix/maintenance/{maintenance_id}")
async def end_maintenance(maintenance_id: str):
    if not maintenance_id.isdigit():
        raise HTTPException(status_code=400, detail="invalid maintenance id")
    found = await _rpc("maintenance.get", {"output": ["maintenanceid", "name"], "maintenanceids": [maintenance_id]})
    if not found:
        return {"ended": False, "reason": "no longer exists"}
    if not found[0]["name"].startswith(NAME_PREFIX):
        raise HTTPException(status_code=403, detail="not a SeyalRun maintenance")
    await _rpc("maintenance.delete", [maintenance_id])
    return {"ended": True}


@router.get("/internal/zabbix/problems")
async def open_problems(host_id: str = Query(min_length=1, max_length=36), min_severity: int = Query(default=3, ge=0, le=5)):
    """Unresolved problems on the host, including ones a maintenance suppresses."""
    hostid = await _zabbix_hostid(host_id)
    rows = await _rpc("problem.get", {"output": ["eventid", "objectid", "name", "severity", "clock"], "hostids": [hostid],
                                      "severities": list(range(min_severity, 6)), "recent": False})
    return {"problems": [{"event_id": p["eventid"], "trigger_id": p["objectid"], "name": p["name"],
                          "severity": int(p["severity"])} for p in rows or []]}
