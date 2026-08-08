"""Outbound mail delivery for MFA email OTPs — SMTP AUTH or Microsoft Graph API,
selected by the singleton ZAMailSettings row (see api/mail_settings.py). Both
providers are supported side-by-side (not just O365) since SMTP AUTH is often
disabled by tenant policy; Graph works regardless of that policy.
"""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ZAMailSettings
from .vault import decrypt


class MailError(Exception):
    pass


async def get_mail_settings(session: AsyncSession) -> ZAMailSettings:
    result = await session.execute(select(ZAMailSettings).limit(1))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = ZAMailSettings()
        session.add(cfg)
        await session.commit()
        await session.refresh(cfg)
    return cfg


def _decrypt_or_blank(v: str) -> str:
    try:
        return decrypt(v) if v else ""
    except Exception:
        return ""


async def send_mail(session: AsyncSession, to: str, subject: str, body: str) -> None:
    cfg = await get_mail_settings(session)
    await send_mail_with_settings(cfg, to, subject, body)


async def send_mail_with_settings(cfg: ZAMailSettings, to: str, subject: str, body: str) -> None:
    """Same as send_mail, but takes an already-resolved ZAMailSettings instead of
    an AsyncSession — for callers sending to several recipients that want to
    resolve settings once (one query) and then fire the actual sends
    concurrently via asyncio.gather. A SQLAlchemy AsyncSession is not safe to
    touch from concurrent coroutines, so those callers can't just wrap
    send_mail() itself in gather()."""
    if not cfg.enabled or not cfg.provider:
        raise MailError("mail delivery is not configured")
    if cfg.provider == "smtp":
        await _send_smtp(cfg, to, subject, body)
    elif cfg.provider == "graph":
        await _send_graph(cfg, to, subject, body)
    else:
        raise MailError(f"unknown mail provider: {cfg.provider!r}")


async def _send_smtp(cfg: ZAMailSettings, to: str, subject: str, body: str) -> None:
    import asyncio

    def _do_send() -> None:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = f"{cfg.from_name} <{cfg.from_address}>" if cfg.from_name else cfg.from_address
        msg["To"] = to
        password = _decrypt_or_blank(cfg.smtp_password)
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15) as smtp:
            if cfg.smtp_use_tls:
                smtp.starttls()
            if cfg.smtp_username:
                smtp.login(cfg.smtp_username, password)
            smtp.sendmail(cfg.from_address, [to], msg.as_string())

    try:
        await asyncio.to_thread(_do_send)
    except Exception as exc:  # noqa: BLE001
        raise MailError(f"SMTP send failed: {exc}") from exc


async def _send_graph(cfg: ZAMailSettings, to: str, subject: str, body: str) -> None:
    client_secret = _decrypt_or_blank(cfg.graph_client_secret)
    token_url = f"https://login.microsoftonline.com/{cfg.graph_tenant_id}/oauth2/v2.0/token"
    sendmail_url = f"https://graph.microsoft.com/v1.0/users/{cfg.graph_sender_upn}/sendMail"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_resp = await client.post(
                token_url,
                data={
                    "client_id": cfg.graph_client_id,
                    "client_secret": client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                    "grant_type": "client_credentials",
                },
            )
            token_resp.raise_for_status()
            access_token = token_resp.json()["access_token"]

            send_resp = await client.post(
                sendmail_url,
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "message": {
                        "subject": subject,
                        "body": {"contentType": "Text", "content": body},
                        "toRecipients": [{"emailAddress": {"address": to}}],
                    },
                    "saveToSentItems": False,
                },
            )
            send_resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise MailError(f"Graph send failed: {exc.response.status_code} {exc.response.text}") from exc
    except Exception as exc:  # noqa: BLE001
        raise MailError(f"Graph send failed: {exc}") from exc
