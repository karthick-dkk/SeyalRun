"""Reverse-proxies authenticated requests to identity-service / inventory-service,
minting a short-lived service-to-service token and forwarding the resolved
caller identity as ``X-User-*`` headers.
"""

from __future__ import annotations

import logging
import time

import httpx
from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from libs.servicetoken import mint

from . import obs
from .config import get_settings

logger = logging.getLogger(__name__)

_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}

# Every trust/identity header this gateway itself sets on the way out. Stripped
# from the CLIENT's inbound headers unconditionally (not just overwritten) —
# proxy() below only assigns some of these conditionally (X-Elevated-Until,
# X-User-Scopes, X-Kiosk-Host-Id are only set when the resolved identity
# actually carries that value), so without this strip a caller could plant a
# forged value that survives untouched whenever the condition is false (e.g. a
# non-elevated admin forging X-Elevated-Until to bypass the JIT-elevation
# gate, or a session-cookie caller forging X-User-Scopes to pass a scoped-PAT
# check). Downstream services must only ever trust these coming from
# api-gateway, never from the original client.
_IDENTITY_HEADERS = {
    "x-service-token", "x-user-id", "x-user-role", "x-user-real-role",
    "x-user-name", "x-kiosk-host-id", "x-user-scopes", "x-elevated-until",
}

# First path segment after /api/v1/ -> (settings attr for upstream base URL, service-token audience)
SERVICE_ROUTES: dict[str, tuple[str, str]] = {
    "auth": ("identity_service_url", "identity-service"),
    "users": ("identity_service_url", "identity-service"),
    "roles": ("identity_service_url", "identity-service"),
    "authorizations": ("identity_service_url", "identity-service"),
    "access-reviews": ("identity_service_url", "identity-service"),
    "command-groups": ("identity_service_url", "identity-service"),
    "command-filters": ("identity_service_url", "identity-service"),
    "login-acls": ("identity_service_url", "identity-service"),
    "api-tokens": ("identity_service_url", "identity-service"),
    "audit": ("identity_service_url", "identity-service"),
    "settings": ("identity_service_url", "identity-service"),
    "hosts": ("inventory_service_url", "inventory-service"),
    "host-groups": ("inventory_service_url", "inventory-service"),
    "credentials": ("inventory_service_url", "inventory-service"),
    "credential-templates": ("inventory_service_url", "inventory-service"),
    "log-backend": ("inventory_service_url", "inventory-service"),
    "zones": ("inventory_service_url", "inventory-service"),
    "ssh": ("terminal_service_url", "terminal-service"),
    "recordings": ("recording_service_url", "recording-service"),
    "projects": ("automation_service_url", "automation-service"),
    "job-templates": ("automation_service_url", "automation-service"),
    "schedules": ("automation_service_url", "automation-service"),
    "job-runs": ("automation_service_url", "automation-service"),
    "secret-management-jobs": ("automation_service_url", "automation-service"),
    "housekeeping": ("automation_service_url", "automation-service"),
    "test-connection": ("automation_service_url", "automation-service"),
    "notifications": ("automation_service_url", "automation-service"),
    "trigger-bindings": ("zabbix_integration_service_url", "zabbix-integration-service"),
    "triggers": ("zabbix_integration_service_url", "zabbix-integration-service"),
    "metrics": ("metrics_service_url", "metrics-service"),
}

from .http import client as _client  # shared pooled client (closed by app lifespan)


def _filtered_headers(headers) -> dict:
    return {
        key: value for key, value in headers.items()
        if key.lower() not in _HOP_BY_HOP and key.lower() not in _IDENTITY_HEADERS
    }


async def proxy(request: Request, path: str, identity: dict) -> Response:
    segment = path.split("/", 1)[0]
    route = SERVICE_ROUTES.get(segment)
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    settings = get_settings()
    base_attr, audience = route
    base_url = getattr(settings, base_attr)

    headers = _filtered_headers(request.headers)
    headers["X-Service-Token"] = mint("api-gateway", audience, settings.service_jwt_secret)
    headers["X-User-Id"] = identity.get("user_id", "")
    headers["X-User-Role"] = identity.get("role", "")
    # Un-elevated role — see main.py's identity["real_role"] comment. Only a handful
    # of downstream resource-scoped checks (e.g. zones.py) need this; everything else
    # keeps using the (possibly write-vouched) X-User-Role as before.
    headers["X-User-Real-Role"] = identity.get("real_role", identity.get("role", ""))
    headers["X-User-Name"] = identity.get("username", "")
    if identity.get("kiosk_host_id"):
        headers["X-Kiosk-Host-Id"] = identity["kiosk_host_id"]
    # PCI DSS Phase C — a Personal Access Token's scopes (see identity-service's
    # api/tokens.py), forwarded so a downstream endpoint can require a specific
    # scope (e.g. "deprovision") beyond the caller's ordinary role capability.
    # Absent entirely for a session-cookie caller (identity.get("scopes") is only
    # ever populated for PAT-based auth — see api-gateway/app/security.py::verify_pat).
    if identity.get("scopes"):
        headers["X-User-Scopes"] = ",".join(identity["scopes"])
    # PCI DSS Phase A JIT elevation — unix timestamp the elevated window expires at,
    # or absent entirely if the user has none active. Downstream services (terminal-
    # service, inventory-service) treat this as the sole source of truth; it is never
    # accepted from the inbound request (identity dict is resolved server-side only).
    if identity.get("elevated_until"):
        headers["X-Elevated-Until"] = str(identity["elevated_until"])

    body = await request.body()
    url = f"{base_url}/api/v1/{path}"

    _t0 = time.monotonic()
    try:
        upstream = await _client.request(
            request.method, url, params=request.query_params, content=body, headers=headers,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        obs.record(audience, (time.monotonic() - _t0) * 1000, is_error=True)
        logger.error("upstream request failed", extra={"upstream": audience})
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"{audience} unreachable") from exc
    obs.record(audience, (time.monotonic() - _t0) * 1000, is_error=upstream.status_code >= 500)

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_filtered_headers(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )


async def filtered_hosts(identity: dict) -> Response:
    """PAM-filtered ``GET /hosts`` for non-admin users.

    Resolves the caller's authorized host_ids/host_group_ids from
    identity-service, fetches the full host list from inventory-service, and
    returns only the hosts the caller is authorized to see.
    """
    settings = get_settings()
    identity_token = mint("api-gateway", "identity-service", settings.service_jwt_secret)
    inventory_token = mint("api-gateway", "inventory-service", settings.service_jwt_secret)

    try:
        authz_resp = await _client.get(
            f"{settings.identity_service_url}/api/v1/internal/authz/host-ids",
            params={"user_id": identity["user_id"]},
            headers={"X-Service-Token": identity_token},
            timeout=10.0,
        )
        authz_resp.raise_for_status()
        authz = authz_resp.json()

        hosts_resp = await _client.get(
            f"{settings.inventory_service_url}/api/v1/hosts",
            headers={"X-Service-Token": inventory_token},
            timeout=10.0,
        )
        hosts_resp.raise_for_status()
        all_hosts = hosts_resp.json()
    except httpx.HTTPError as exc:
        logger.error("PAM host filter: upstream request failed")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="upstream service unreachable") from exc

    if authz.get("is_admin"):
        return JSONResponse(content=all_hosts)

    host_ids = set(authz.get("host_ids", []))
    host_group_ids = set(authz.get("host_group_ids", []))

    allowed = [
        h for h in all_hosts
        if h["id"] in host_ids or any(g in host_group_ids for g in h.get("group_ids", []))
    ]
    return JSONResponse(content=allowed)


async def kiosk_filtered_hosts(kiosk_host_id: str) -> Response:
    """A kiosk login's ``GET /hosts`` never returns more than the one host it is
    bound to — defense in depth beyond hiding the host panel in the UI, so a raw
    API call with a kiosk token can't be used to enumerate other hosts either.
    Takes precedence over the normal PAM filter regardless of role."""
    settings = get_settings()
    inventory_token = mint("api-gateway", "inventory-service", settings.service_jwt_secret)
    try:
        resp = await _client.get(
            f"{settings.inventory_service_url}/api/v1/hosts",
            headers={"X-Service-Token": inventory_token},
            timeout=10.0,
        )
        resp.raise_for_status()
        all_hosts = resp.json()
    except httpx.HTTPError as exc:
        logger.error("kiosk host filter: upstream request failed")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="upstream service unreachable") from exc
    return JSONResponse(content=[h for h in all_hosts if h["id"] == kiosk_host_id])
