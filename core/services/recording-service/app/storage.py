"""S3 storage backend for tiered recordings (v1.1 Feature 4).

boto3 is blocking, so each call runs in a thread executor. All functions are safe
no-ops / raise clearly when S3 is not configured — staging without S3 keeps working
(recordings stay 'local'; the tier job simply skips)."""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
from types import SimpleNamespace
from typing import Any

import httpx

from libs.servicetoken import mint

logger = logging.getLogger(__name__)


def s3_configured(settings: Any) -> bool:
    return bool(settings.s3_bucket and settings.s3_access_key_id and settings.s3_secret_access_key)


async def resolve_recording_s3(settings: Any) -> Any | None:
    """Where should session recordings tier to? Ask the admin's Log Backend config
    (owned by inventory-service). Return an S3-settings object only when the
    'recording' category is routed to S3 and creds are present; otherwise None so
    the tier job cleanly skips. Falls back to this service's own env S3 config if
    inventory is unreachable, so tiering keeps working during an inventory blip."""
    try:
        token = mint("recording-service", "inventory-service", settings.service_jwt_secret)
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{settings.inventory_service_url}/api/v1/log-backend/internal/shipping",
                headers={"X-Service-Token": token},
            )
            resp.raise_for_status()
            cfg = resp.json()
    except httpx.HTTPError as e:
        logger.warning("recording tier: could not read log-backend config (%s) — using env S3", e)
        return settings if s3_configured(settings) else None

    routing = cfg.get("routing") or {}
    recording_targets = routing.get("recording")
    # No explicit routing yet → fall back to the legacy single backend field.
    if recording_targets is None:
        recording_targets = {"s3": ["s3"], "es+s3": ["s3"]}.get(cfg.get("backend", ""), [])
    if "s3" not in recording_targets:
        return None  # admin did not route recordings to S3

    s3 = SimpleNamespace(
        s3_bucket=cfg.get("s3_bucket", ""), s3_region=cfg.get("s3_region", ""),
        s3_access_key_id=cfg.get("s3_access_key_id", ""),
        s3_secret_access_key=cfg.get("s3_secret_access_key", ""),
        s3_endpoint_url=cfg.get("s3_endpoint_url", ""),
    )
    return s3 if s3_configured(s3) else None


def _client(settings: Any):
    import boto3
    kwargs: dict[str, Any] = {
        "aws_access_key_id": settings.s3_access_key_id,
        "aws_secret_access_key": settings.s3_secret_access_key,
    }
    if settings.s3_region:
        kwargs["region_name"] = settings.s3_region
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
    return boto3.client("s3", **kwargs)


def _put(settings: Any, key: str, frames: list) -> None:
    body = gzip.compress(json.dumps(frames).encode())
    _client(settings).put_object(Bucket=settings.s3_bucket, Key=key, Body=body, ContentType="application/gzip")


def _get(settings: Any, key: str) -> list:
    obj = _client(settings).get_object(Bucket=settings.s3_bucket, Key=key)
    return json.loads(gzip.decompress(obj["Body"].read()).decode())


def _presign(settings: Any, key: str, expires: int) -> str:
    return _client(settings).generate_presigned_url(
        "get_object", Params={"Bucket": settings.s3_bucket, "Key": key}, ExpiresIn=expires
    )


async def write_to_s3(rec_id: str, frames: list, settings: Any) -> str:
    key = f"recordings/{rec_id}.json.gz"
    await asyncio.get_event_loop().run_in_executor(None, _put, settings, key, frames)
    return key


async def read_from_s3(storage_key: str, settings: Any) -> list:
    return await asyncio.get_event_loop().run_in_executor(None, _get, settings, storage_key)


async def presigned_url(storage_key: str, settings: Any, expires: int = 3600) -> str:
    return await asyncio.get_event_loop().run_in_executor(None, _presign, settings, storage_key, expires)
