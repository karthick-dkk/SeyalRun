"""Agentless self-monitoring endpoint for the Zabbix HTTP-agent template.

GET /api/v1/webhook/zabbix/monitor (edge-proxy: /webhook/zabbix/monitor) fans
out concurrently to every service in monitoring/services.json (health +
Prometheus /metrics) and returns one aggregate JSON document. The template's
master HTTP-agent item polls this once per interval; discovery and item
prototypes slice it with JSONPath, so Zabbix makes exactly one HTTP request
per interval regardless of how many services the stack runs.

Auth: X-Monitor-Token must equal ZABBIX_WEBHOOK_HMAC_SECRET (constant-time
compare). Like the webhook, edge-proxy routes this straight past the
api-gateway, so the token check is this endpoint's ONLY authentication.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Header, HTTPException, status

from app.config import get_settings
from app.monitorlib import classify_health, parse_seyalrun_metrics

router = APIRouter()
logger = logging.getLogger(__name__)

# /app/app/api/monitor.py -> /app/monitoring/services.json (Dockerfile COPY)
_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "monitoring" / "services.json"
_registry: list[dict] | None = None


def _services() -> list[dict]:
    global _registry
    if _registry is None:
        _registry = json.loads(_REGISTRY_PATH.read_text())["services"]
    return _registry


async def _probe(client: httpx.AsyncClient, svc: dict) -> dict:
    entry: dict = {
        "name": svc["name"],
        "health": "down",
        "has_metrics": svc.get("metrics_url") is not None,
    }
    try:
        resp = await client.get(svc["health_url"])
        entry["health"] = classify_health(resp.status_code, resp.text)
    except httpx.HTTPError:
        pass
    if entry["has_metrics"]:
        body = ""
        try:
            resp = await client.get(svc["metrics_url"])
            if resp.status_code == 200:
                body = resp.text
        except httpx.HTTPError:
            pass
        entry["metrics"] = parse_seyalrun_metrics(body)
    return entry


@router.get("/webhook/zabbix/monitor")
async def zabbix_monitor(
    x_monitor_token: str = Header(default="", alias="X-Monitor-Token"),
) -> dict:
    secret = get_settings().zabbix_webhook_hmac_secret
    if not secret or not hmac.compare_digest(x_monitor_token, secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid monitor token"
        )
    async with httpx.AsyncClient(timeout=5.0) as client:
        services = await asyncio.gather(*(_probe(client, s) for s in _services()))
    return {"services": list(services)}
