"""webhook-receiver — receive Zabbix action webhooks and trigger zbx-sync."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI, HTTPException, Request, status
from pravesh_shared.health import register_readiness_check
from pravesh_shared.health import router as health_router
from pravesh_shared.log import configure_logging
from pravesh_shared.middleware import RequestContextMiddleware
from prometheus_client import Counter, make_asgi_app

configure_logging("webhook-receiver", os.getenv("WR_LOG_LEVEL", "INFO"))
log = structlog.get_logger(__name__)

HMAC_SECRET = os.getenv("WR_WEBHOOK_HMAC_SECRET", "")
REDIS_URL = os.getenv("WR_REDIS_URL", "redis://localhost:6379/4")
ZBX_SYNC_URL = os.getenv("WR_ZBX_SYNC_URL", "http://localhost:8002")
DEDUP_WINDOW = 300  # 5 minutes

WEBHOOKS_RECEIVED = Counter("webhooks_received_total", "Webhooks received", ["status"])

_redis: aioredis.Redis | None = None
_event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
_worker_task: asyncio.Task | None = None


async def check_redis() -> bool:
    assert _redis is not None
    await _redis.ping()
    return True


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _redis, _worker_task
    _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    register_readiness_check("redis", check_redis)
    _worker_task = asyncio.create_task(event_worker())
    log.info("webhook_receiver_starting")
    yield
    if _worker_task:
        _worker_task.cancel()
    await _redis.aclose()


app = FastAPI(title="webhook-receiver", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
app.include_router(health_router)
app.mount("/metrics", make_asgi_app())


def verify_hmac(body: bytes, signature: str) -> bool:
    """Constant-time HMAC verification."""
    if not HMAC_SECRET:
        return True  # HMAC disabled if no secret configured
    expected = "sha256=" + hmac.new(HMAC_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.post("/", status_code=status.HTTP_202_ACCEPTED)
async def receive_webhook(request: Request) -> dict:
    """Accept Zabbix action webhooks. Verifies HMAC, deduplicates, enqueues."""
    body = await request.body()
    sig = request.headers.get("X-Pravesh-Signature", "")

    if HMAC_SECRET and not verify_hmac(body, sig):
        WEBHOOKS_RECEIVED.labels(status="invalid_hmac").inc()
        log.warning(
            "invalid_hmac_signature",
            client=request.client.host if request.client else "?",
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")

    # Deduplication by Zabbix event ID
    event_id = str(payload.get("event_id", payload.get("eventid", "")))
    if event_id and _redis:
        key = f"webhook_dedup:{event_id}"
        if await _redis.exists(key):
            log.info("webhook_duplicate_skipped", event_id=event_id)
            WEBHOOKS_RECEIVED.labels(status="duplicate").inc()
            return {"status": "duplicate", "event_id": event_id}
        await _redis.setex(key, DEDUP_WINDOW, "1")

    await _event_queue.put(payload)
    WEBHOOKS_RECEIVED.labels(status="accepted").inc()
    log.info("webhook_accepted", event_id=event_id, queue_size=_event_queue.qsize())
    return {"status": "accepted", "event_id": event_id}


async def event_worker() -> None:
    """Background worker: process queued events and trigger sync."""
    while True:
        try:
            event = await _event_queue.get()
            await process_event(event)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("event_worker_error", error=str(exc))


async def process_event(event: dict[str, Any]) -> None:
    """Process a single Zabbix event — trigger zbx-sync."""
    event_type = event.get("event_type", event.get("type", "unknown"))
    log.info("processing_event", event_type=event_type)

    try:
        async with httpx.AsyncClient(verify=False, timeout=10) as client:
            r = await client.post(f"{ZBX_SYNC_URL}/sync/run", params={"dry_run": False})
            log.info("sync_triggered", status=r.status_code)
    except Exception as exc:
        log.warning("sync_trigger_failed", error=str(exc))
