"""WebSocket endpoint — streams live job output via Redis pub/sub."""

from __future__ import annotations

import asyncio
import json

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from ..config import get_settings

log = structlog.get_logger(__name__)
settings = get_settings()

router = APIRouter(tags=["streaming"])

_MAX_STREAM_SECONDS = 3600


@router.websocket("/ws/jobs/{job_id}/stream")
async def stream_job_output(websocket: WebSocket, job_id: str, token: str = "") -> None:
    """
    Stream live Ansible output for a running job.
    Auth: pass token as query param ?token=<bearer_token>
    Messages: JSON objects with {type, line} or {type, status, exit_code}
    """
    await websocket.accept()

    if not token:
        await websocket.send_json({"type": "error", "line": "Missing token"})
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Validate token reusing the same Redis-cached JMS validation
    try:
        import redis.asyncio as aioredis

        redis = aioredis.from_url(settings.redis_url, decode_responses=True)

        cache_key = f"jms_token:{token[:16]}"
        cached = await redis.get(cache_key)
        if not cached:
            # Must have a valid cached session — we don't re-validate via HTTP in WS
            await websocket.send_json({"type": "error", "line": "Unauthorized"})
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            await redis.aclose()
            return

        await _stream_redis_channel(websocket, job_id, redis)
        await redis.aclose()

    except WebSocketDisconnect:
        log.info("ws_client_disconnected", job_id=job_id)
    except Exception as exc:
        log.error("ws_stream_error", job_id=job_id, error=str(exc))
        try:
            await websocket.send_json({"type": "error", "line": str(exc)})
            await websocket.close()
        except Exception:
            pass


async def _stream_redis_channel(websocket: WebSocket, job_id: str, redis) -> None:
    channel_name = f"job_output:{job_id}"
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel_name)

    # Send any buffered output lines already stored in DB
    await _flush_existing_output(websocket, job_id)

    deadline = asyncio.get_event_loop().time() + _MAX_STREAM_SECONDS

    try:
        while asyncio.get_event_loop().time() < deadline:
            message = await asyncio.wait_for(
                pubsub.get_message(ignore_subscribe_messages=True), timeout=1.0
            )
            if message is None:
                # Send keepalive ping
                await websocket.send_json({"type": "ping"})
                continue

            data = message.get("data", "")
            try:
                payload = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                payload = {"type": "line", "line": str(data)}

            await websocket.send_json(payload)

            if payload.get("type") == "done":
                break

    finally:
        await pubsub.unsubscribe(channel_name)
        await pubsub.aclose()


async def _flush_existing_output(websocket: WebSocket, job_id: str) -> None:
    """Send already-captured output lines before subscribing to pub/sub."""
    try:
        import uuid

        from ..database import AsyncSessionLocal
        from ..models.domain import Job

        async with AsyncSessionLocal() as db:
            try:
                uid = uuid.UUID(job_id)
            except ValueError:
                return

            j = await db.get(Job, uid)
            if not j:
                return

            for line in j.output_lines or []:
                await websocket.send_json({"type": "line", "line": line})

            if j.status in ("success", "failed", "cancelled"):
                await websocket.send_json(
                    {
                        "type": "done",
                        "status": j.status,
                        "exit_code": j.exit_code,
                    }
                )
    except Exception as exc:
        log.warning("flush_existing_output_error", job_id=job_id, error=str(exc))
