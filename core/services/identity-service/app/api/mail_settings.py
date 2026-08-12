"""Mail delivery config for MFA email OTPs (O365/SMTP or Microsoft Graph).

Single-row config, same shape as inventory-service's log-backend admin config.
smtp_password / graph_client_secret are vault-encrypted at rest and never
returned in clear (masked on GET)."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import require_admin, require_service_token, require_superadmin
from ..mailer import MailError, get_mail_settings, send_mail
from ..vault import encrypt

router = APIRouter(prefix="/settings/mail", tags=["mail-settings"], dependencies=[Depends(require_service_token)])

_MASK = "••••••••"


def _mask(val: str) -> str:
    return _MASK if val else ""


def _public(cfg) -> dict:
    return {
        "provider": cfg.provider,
        "enabled": cfg.enabled,
        "from_address": cfg.from_address,
        "from_name": cfg.from_name,
        "smtp_host": cfg.smtp_host,
        "smtp_port": cfg.smtp_port,
        "smtp_username": cfg.smtp_username,
        "smtp_password": _mask(cfg.smtp_password),
        "smtp_use_tls": cfg.smtp_use_tls,
        "graph_tenant_id": cfg.graph_tenant_id,
        "graph_client_id": cfg.graph_client_id,
        "graph_client_secret": _mask(cfg.graph_client_secret),
        "graph_sender_upn": cfg.graph_sender_upn,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


class MailSettingsIn(BaseModel):
    provider: str = ""  # "smtp" | "graph" | ""
    enabled: bool = False
    from_address: str = ""
    from_name: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str | None = None  # None/unchanged → keep existing; "" clears
    smtp_use_tls: bool = True
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str | None = None
    graph_sender_upn: str = ""


class TestEmailIn(BaseModel):
    to: str


@router.get("", dependencies=[Depends(require_admin)])
async def get_settings(session: AsyncSession = Depends(get_session)):
    cfg = await get_mail_settings(session)
    return _public(cfg)


@router.put("", dependencies=[Depends(require_superadmin)])
async def put_settings(payload: MailSettingsIn, session: AsyncSession = Depends(get_session)):
    cfg = await get_mail_settings(session)
    cfg.provider = payload.provider
    cfg.enabled = payload.enabled
    cfg.from_address = payload.from_address
    cfg.from_name = payload.from_name
    cfg.smtp_host = payload.smtp_host
    cfg.smtp_port = payload.smtp_port
    cfg.smtp_username = payload.smtp_username
    cfg.smtp_use_tls = payload.smtp_use_tls
    cfg.graph_tenant_id = payload.graph_tenant_id
    cfg.graph_client_id = payload.graph_client_id
    cfg.graph_sender_upn = payload.graph_sender_upn
    if payload.smtp_password is not None and payload.smtp_password != _MASK:
        cfg.smtp_password = encrypt(payload.smtp_password) if payload.smtp_password else ""
    if payload.graph_client_secret is not None and payload.graph_client_secret != _MASK:
        cfg.graph_client_secret = encrypt(payload.graph_client_secret) if payload.graph_client_secret else ""
    await session.commit()
    await session.refresh(cfg)
    return _public(cfg)


@router.post("/test", dependencies=[Depends(require_superadmin)])
async def test_mail(payload: TestEmailIn, session: AsyncSession = Depends(get_session)):
    t = time.perf_counter()
    try:
        await send_mail(session, payload.to, "SeyalRun test email", "This is a test email from SeyalRun's mail settings.")
        return {"ok": True, "latency_ms": round((time.perf_counter() - t) * 1000), "error": ""}
    except MailError as exc:
        return {"ok": False, "latency_ms": round((time.perf_counter() - t) * 1000), "error": str(exc)}
