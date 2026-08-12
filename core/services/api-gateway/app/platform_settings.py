"""Live-reloaded, DB-first platform settings — superadmin-editable via
/api/v1/settings/platform and /settings/zabbix-module (identity-service),
applied here without a gateway restart.

Mirrors the existing `_load_role_perms` / `_roles_refresh_loop` pattern in
main.py: a background task polls identity-service's internal endpoints every
~40s and populates a module-level cache; every read here falls back to the
static .env value (`config.Settings`) whenever the DB has no override yet or
identity-service is unreachable, so a cold cache never breaks the gateway.
"""

from __future__ import annotations

import logging

import httpx

from libs.servicetoken import mint

from . import http as _http
from .config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()

_platform: dict = {}
_zabbix_module: dict = {}


async def refresh() -> None:
    global _platform, _zabbix_module
    token = mint("api-gateway", "identity-service", _settings.service_jwt_secret)
    headers = {"X-Service-Token": token}
    try:
        r = await _http.client.get(
            f"{_settings.identity_service_url}/api/v1/internal/settings/platform",
            headers=headers, timeout=4.0,
        )
        if r.status_code == 200:
            _platform = r.json() or {}
    except httpx.HTTPError as exc:
        logger.warning("platform settings refresh failed: %s", exc)

    try:
        r = await _http.client.get(
            f"{_settings.identity_service_url}/api/v1/internal/settings/zabbix-module",
            headers=headers, timeout=4.0,
        )
        if r.status_code == 200:
            _zabbix_module = r.json() or {}
    except httpx.HTTPError as exc:
        logger.warning("zabbix-module settings refresh failed: %s", exc)


def rate_limit_requests() -> int:
    return int(_platform.get("rate_limit_requests") or _settings.rate_limit_requests)


def rate_limit_window_seconds() -> int:
    return int(_platform.get("rate_limit_window_seconds") or _settings.rate_limit_window_seconds)


def session_idle_minutes() -> int:
    return int(_platform.get("session_idle_minutes") or _settings.session_idle_minutes)


def session_absolute_hours() -> int:
    return int(_platform.get("session_absolute_hours") or _settings.session_absolute_hours)


def zabbix_module_enabled() -> bool:
    return bool(_zabbix_module.get("enabled", False))


def zabbix_module_trusted_ips() -> list[str]:
    return list(_zabbix_module.get("trusted_ips") or [])


def zabbix_module_elevated_rate_limit() -> int:
    # Default elevated bucket is 10x the ordinary per-user limit if never configured.
    return int(_zabbix_module.get("elevated_rate_limit") or (_settings.rate_limit_requests * 10))
