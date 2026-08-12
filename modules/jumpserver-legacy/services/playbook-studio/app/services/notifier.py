"""Notification delivery — HMAC-signed webhooks + SMTP email via aiosmtplib."""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import aiosmtplib
import httpx
import structlog

from ..config import get_settings
from ..models.domain import AlertHistory, AlertRule, NotificationChannel

log = structlog.get_logger(__name__)
settings = get_settings()


# ── Public entry points ───────────────────────────────────────────────────────


async def deliver_alert(
    rule: AlertRule,
    event_type: str,
    event_payload: dict[str, Any],
    db,
) -> None:
    """Dispatch alert to all channels configured in the rule. Writes AlertHistory rows."""
    channels = rule.channels or []
    if not channels:
        return

    channels_tried: list[dict] = []
    any_success = False
    error_detail: str | None = None

    for ch_cfg in channels:
        ch_type = ch_cfg.get("type", "webhook")
        result = {"type": ch_type, "success": False, "error": None}

        try:
            if ch_type == "webhook":
                await _send_webhook(ch_cfg, rule, event_type, event_payload)
            elif ch_type == "email":
                await _send_email(ch_cfg, rule, event_type, event_payload)
            else:
                result["error"] = f"Unknown channel type: {ch_type}"
                channels_tried.append(result)
                continue

            result["success"] = True
            any_success = True
        except Exception as exc:
            result["error"] = str(exc)
            error_detail = str(exc)
            log.warning(
                "alert_delivery_failed", rule_id=str(rule.id), channel=ch_type, error=str(exc)
            )

        channels_tried.append(result)

    history = AlertHistory(
        rule_id=rule.id,
        rule_name=rule.name,
        event_type=event_type,
        event_payload=event_payload,
        channels_tried=channels_tried,
        delivery_status="delivered" if any_success else "failed",
        delivered_at=datetime.datetime.utcnow(),
        error_detail=error_detail,
    )
    db.add(history)
    await db.flush()


async def fire_test_alert(rule: AlertRule, triggered_by: str) -> dict:
    """Send a test alert payload through all configured channels without writing DB history."""
    test_payload = _build_payload(
        event_type=rule.event_type,
        rule=rule,
        details={
            "test": True,
            "triggered_by": triggered_by,
            "message": "This is a test alert from SeyalRun",
        },
        severity="info",
    )

    results = []
    for ch_cfg in rule.channels or []:
        ch_type = ch_cfg.get("type", "webhook")
        try:
            if ch_type == "webhook":
                await _send_webhook(ch_cfg, rule, rule.event_type, test_payload)
            elif ch_type == "email":
                await _send_email(ch_cfg, rule, rule.event_type, test_payload)
            results.append({"type": ch_type, "success": True})
        except Exception as exc:
            results.append({"type": ch_type, "success": False, "error": str(exc)})

    return {"channels": results}


async def test_channel_delivery(channel: NotificationChannel, triggered_by: str) -> dict:
    """Test a standalone notification channel."""
    ch_cfg = {"type": channel.channel_type, **channel.config}
    test_payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": "test",
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "severity": "info",
        "message": "Test delivery from SeyalRun",
        "triggered_by": triggered_by,
    }

    try:
        if channel.channel_type == "webhook":
            await _send_webhook(ch_cfg, None, "test", test_payload)
        elif channel.channel_type == "email":
            await _send_email(ch_cfg, None, "test", test_payload)
        else:
            return {"success": False, "error": f"Unknown type: {channel.channel_type}"}
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ── Webhook delivery ──────────────────────────────────────────────────────────


async def _send_webhook(
    ch_cfg: dict,
    rule: AlertRule | None,
    event_type: str,
    event_payload: dict,
) -> None:
    url = ch_cfg.get("url", "")
    if not url:
        raise ValueError("Webhook URL is required")

    secret = ch_cfg.get("secret", "")
    body_bytes = json.dumps(event_payload).encode()
    sig = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest() if secret else ""

    headers = {
        "Content-Type": "application/json",
        "X-SeyalRun-Event": event_type,
        "X-SeyalRun-Rule": rule.name if rule else "test",
    }
    if sig:
        headers["X-SeyalRun-Signature"] = f"sha256={sig}"

    async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as client:
        resp = await client.post(url, content=body_bytes, headers=headers)
        resp.raise_for_status()

    log.info("webhook_delivered", url=url, event=event_type, status=resp.status_code)


# ── Email delivery ────────────────────────────────────────────────────────────


async def _send_email(
    ch_cfg: dict,
    rule: AlertRule | None,
    event_type: str,
    event_payload: dict,
) -> None:
    to_addresses = ch_cfg.get("to", [])
    if not to_addresses:
        raise ValueError("Email 'to' addresses are required")

    subject = ch_cfg.get("subject") or f"[SeyalRun Alert] {event_type.replace('_', ' ').title()}"
    rule_name = rule.name if rule else "Test"

    html_body = _render_email_html(rule_name, event_type, event_payload)
    text_body = _render_email_text(rule_name, event_type, event_payload)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from_address
    msg["To"] = ", ".join(to_addresses)
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username or None,
        password=settings.smtp_password or None,
        use_tls=settings.smtp_use_tls,
        start_tls=settings.smtp_start_tls,
    )

    log.info("email_delivered", to=to_addresses, event=event_type)


# ── Payload builders ──────────────────────────────────────────────────────────


def _build_payload(
    event_type: str,
    rule: AlertRule | None,
    details: dict,
    severity: str = "warning",
) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "severity": severity,
        "rule": {"id": str(rule.id) if rule else None, "name": rule.name if rule else None},
        "details": details,
        "platform": "seyalrun-pam",
        "instance_url": f"http://{settings.jumpserver_api_url}",
    }


def _render_email_html(rule_name: str, event_type: str, payload: dict) -> str:
    details_rows = "".join(
        f"<tr><td style='padding:4px 8px;font-weight:bold'>{k}</td>"
        f"<td style='padding:4px 8px'>{v}</td></tr>"
        for k, v in payload.get("details", {}).items()
    )
    return f"""
<html><body style="font-family:monospace;background:#0d1117;color:#e6edf3;padding:24px">
<h2 style="color:#58a6ff">[SeyalRun] {event_type.replace("_", " ").title()}</h2>
<p><strong>Rule:</strong> {rule_name}</p>
<p><strong>Time:</strong> {payload.get("timestamp", "")}</p>
<p><strong>Severity:</strong> {payload.get("severity", "")}</p>
<table style="border-collapse:collapse;width:100%">{details_rows}</table>
<hr style="border-color:#21262d;margin-top:24px">
<p style="color:#8b949e;font-size:12px">SeyalRun — Automated Alert</p>
</body></html>"""


def _render_email_text(rule_name: str, event_type: str, payload: dict) -> str:
    lines = [
        f"[SeyalRun] {event_type}",
        f"Rule: {rule_name}",
        f"Time: {payload.get('timestamp', '')}",
        f"Severity: {payload.get('severity', '')}",
        "",
    ]
    for k, v in payload.get("details", {}).items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)
