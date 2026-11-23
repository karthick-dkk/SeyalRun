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

import asyncio
import logging
import time

import httpx

from libs import mcpsettings
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


# ── MCP (AI-agent) settings ─────────────────────────────────────────────────
# Fetched on demand with a short cache instead of riding the 40 s loop above: a
# superadmin turning MCP off expects agents to stop within seconds. mcp-server
# caches GET /api/v1/mcp/policy for MCP_CACHE_SECONDS as well, so the worst case
# from save to effect is about twice this. A PUT through the gateway also
# invalidates it at once (main.py).
#
# On a read failure keep the LAST KNOWN value. If nothing has ever loaded
# (gateway just started and identity-service is unreachable) return None: the
# callers then FAIL CLOSED for MCP calls and agent-started runs, because an
# admin's restrictions must not silently revert to the permissive defaults.
# Concurrent callers wait on one fetch instead of seeing a half-loaded state.
MCP_CACHE_SECONDS = 5.0
_mcp: dict | None = None
_mcp_at: float = 0.0
_mcp_lock = asyncio.Lock()


async def _fetch_mcp() -> None:
    global _mcp
    try:
        token = mint("api-gateway", "identity-service", _settings.service_jwt_secret)
        r = await _http.client.get(
            f"{_settings.identity_service_url}/api/v1/internal/settings/mcp",
            headers={"X-Service-Token": token}, timeout=3.0,
        )
        if r.status_code == 200:
            _mcp = mcpsettings.normalize(r.json())
        else:
            logger.warning("mcp settings refresh: identity-service returned %s", r.status_code)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("mcp settings refresh failed, keeping last known: %s", exc)


async def mcp_settings() -> dict | None:
    """Current MCP settings, or None when they have never been loaded (fail closed)."""
    global _mcp_at
    if _mcp_at and time.monotonic() - _mcp_at < MCP_CACHE_SECONDS and _mcp is not None:
        return dict(_mcp)
    async with _mcp_lock:
        # Another caller may have refreshed while we waited.
        if not (_mcp_at and time.monotonic() - _mcp_at < MCP_CACHE_SECONDS and _mcp is not None):
            await _fetch_mcp()
            _mcp_at = time.monotonic()
    return dict(_mcp) if _mcp is not None else None


def invalidate_mcp_settings() -> None:
    global _mcp_at
    _mcp_at = 0.0
