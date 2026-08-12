"""Centralized log backend admin config (v1.1 Feature 1).

Single-row config in the inventory DB. es_api_key / s3_secret_access_key are vault-encrypted
at rest and never returned in clear (masked on GET)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.securelog.backends import test_elasticsearch, test_s3

from ..database import get_session
from ..deps import require_admin, require_service_token
from ..models import ZALogBackendConfig
from ..vault import decrypt, encrypt

router = APIRouter(prefix="/log-backend", tags=["log-backend"], dependencies=[Depends(require_service_token)])

_SECRET_FIELDS = ("es_api_key", "s3_secret_access_key")


async def _get_or_create(session: AsyncSession) -> ZALogBackendConfig:
    result = await session.execute(select(ZALogBackendConfig).limit(1))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = ZALogBackendConfig()
        session.add(cfg)
        await session.commit()
        await session.refresh(cfg)
    return cfg


def _mask(val: str) -> str:
    return "••••••••" if val else ""


def _public(cfg: ZALogBackendConfig) -> dict:
    return {
        "backend": cfg.backend,
        "es_url": cfg.es_url,
        "es_api_key": _mask(cfg.es_api_key),
        "es_index_prefix": cfg.es_index_prefix,
        "es_verify_ssl": cfg.es_verify_ssl,
        "s3_bucket": cfg.s3_bucket,
        "s3_region": cfg.s3_region,
        "s3_access_key_id": cfg.s3_access_key_id,
        "s3_secret_access_key": _mask(cfg.s3_secret_access_key),
        "s3_endpoint_url": cfg.s3_endpoint_url,
        "routing": cfg.routing or {},
        "retention": cfg.retention or {},
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


def _decrypted(cfg: ZALogBackendConfig) -> dict:
    def d(v: str) -> str:
        try:
            return decrypt(v) if v else ""
        except Exception:
            return ""
    return {
        "es_url": cfg.es_url, "es_api_key": d(cfg.es_api_key), "es_index_prefix": cfg.es_index_prefix,
        "es_verify_ssl": cfg.es_verify_ssl,
        "s3_bucket": cfg.s3_bucket, "s3_region": cfg.s3_region,
        "s3_access_key_id": cfg.s3_access_key_id, "s3_secret_access_key": d(cfg.s3_secret_access_key),
        "s3_endpoint_url": cfg.s3_endpoint_url, "routing": cfg.routing or {},
        "retention": cfg.retention or {},
    }


class LogBackendIn(BaseModel):
    backend: str = "local"
    es_url: str = ""
    es_api_key: str | None = None  # None / unchanged → keep existing; "" clears
    es_index_prefix: str = "seyalrun"
    es_verify_ssl: bool = True
    s3_bucket: str = ""
    s3_region: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str | None = None
    s3_endpoint_url: str = ""
    routing: dict = {}
    retention: dict = {}


@router.get("", dependencies=[Depends(require_admin)])
async def get_config(session: AsyncSession = Depends(get_session)):
    cfg = await _get_or_create(session)
    return _public(cfg)


@router.put("", dependencies=[Depends(require_admin)])
async def put_config(payload: LogBackendIn, session: AsyncSession = Depends(get_session)):
    cfg = await _get_or_create(session)
    cfg.backend = payload.backend
    cfg.es_url = payload.es_url
    cfg.es_index_prefix = payload.es_index_prefix
    cfg.es_verify_ssl = payload.es_verify_ssl
    cfg.s3_bucket = payload.s3_bucket
    cfg.s3_region = payload.s3_region
    cfg.s3_access_key_id = payload.s3_access_key_id
    cfg.s3_endpoint_url = payload.s3_endpoint_url
    cfg.routing = payload.routing or {}
    cfg.retention = payload.retention or {}
    # Secrets: only overwrite when a non-masked value is supplied.
    if payload.es_api_key is not None and payload.es_api_key != "••••••••":
        cfg.es_api_key = encrypt(payload.es_api_key) if payload.es_api_key else ""
    if payload.s3_secret_access_key is not None and payload.s3_secret_access_key != "••••••••":
        cfg.s3_secret_access_key = encrypt(payload.s3_secret_access_key) if payload.s3_secret_access_key else ""
    await session.commit()
    await session.refresh(cfg)
    return _public(cfg)


@router.get("/internal/shipping")
async def internal_shipping_config(session: AsyncSession = Depends(get_session)):
    """Service-to-service view of the log backend (decrypted S3/ES creds + routing).
    Router-level require_service_token guards this; NO require_admin, so peers like
    recording-service can resolve where the 'recording' category should ship."""
    cfg = await _get_or_create(session)
    dec = _decrypted(cfg)
    dec["backend"] = cfg.backend
    return dec


@router.post("/test", dependencies=[Depends(require_admin)])
async def test_connection(session: AsyncSession = Depends(get_session)):
    cfg = await _get_or_create(session)
    dec = _decrypted(cfg)
    # Test every backend actually in use — the legacy `backend` field AND anything
    # referenced by the content-routing matrix (a category may target ES/S3 even
    # when the top-level backend is "local").
    used = {b for targets in (cfg.routing or {}).values() for b in (targets or [])}
    legacy = {"elasticsearch": {"elasticsearch"}, "s3": {"s3"},
              "es+s3": {"elasticsearch", "s3"}}.get(cfg.backend, set())
    used |= legacy
    out: dict = {}
    if "elasticsearch" in used:
        out["elasticsearch"] = await test_elasticsearch(dec["es_url"], dec["es_api_key"], verify_ssl=dec.get("es_verify_ssl", True))
    if "s3" in used:
        out["s3"] = await test_s3(dec)
    if not out:
        out["local"] = {"ok": True, "latency_ms": 0, "error": ""}
    return out
