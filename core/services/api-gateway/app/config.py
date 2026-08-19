from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from libs.secrets import require_secrets


def _ws_scheme(url: str) -> str:
    """http://host -> ws://host, https://host -> wss://host."""
    for http, ws in (("https://", "wss://"), ("http://", "ws://")):
        if url.startswith(http):
            return ws + url[len(http):]
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "api-gateway"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    log_path: str = "/var/log/seyalrun/api-gateway.jsonl"

    service_jwt_secret: str = ""

    # Server-side session lookup (see security.py::lookup_session) — sliding
    # idle timeout re-armed on every request, capped at the absolute ceiling
    # from the session's creation time regardless of activity.
    redis_url: str = "redis://redis:6379/0"
    session_idle_minutes: int = 30
    session_absolute_hours: int = 8

    identity_service_url: str = "http://identity-service:8101"
    inventory_service_url: str = "http://inventory-service:8102"
    terminal_service_url: str = "http://terminal-service:8103"
    terminal_service_ws_url: str = ""   # derived — see _derive_ws_urls
    recording_service_url: str = "http://recording-service:8104"
    automation_service_url: str = "http://automation-service:8105"
    automation_service_ws_url: str = ""  # derived — see _derive_ws_urls
    zabbix_integration_service_url: str = "http://zabbix-integration-service:8106"
    metrics_service_url: str = "http://metrics-service:8107"

    # Zabbix integration (read from the same .env) — surfaced to the UI for the header
    # "Zabbix" link and the Integration admin page. Token is never exposed to the browser.
    zabbix_api_url: str = ""
    zabbix_console_url: str = ""

    frontend_origin: str = "https://localhost"

    # The SPA fires 6–8 API calls per page (nav + hosts + zones + creds + authz + sessions),
    # so the per-user/IP budget must comfortably cover rapid navigation and refreshes.
    rate_limit_requests: int = 600
    rate_limit_window_seconds: int = 60

    # Metrics
    metrics_cache_ttl_seconds: int = 30
    metrics_refresh_interval_seconds: int = 30

    @model_validator(mode="after")
    def _derive_ws_urls(self) -> "Settings":
        """Keep each WS upstream's scheme in lockstep with its HTTP one.

        These used to be two independent settings with independent defaults, and
        docker-compose.internal-tls.yml overrides only the http:// ones. So with
        internal TLS enabled the gateway kept dialling ws:// at a TLS-only
        listener and *every* WebSocket feature broke at once — SSH terminal, job
        logs, notifications — each failing with "did not receive a valid HTTP
        response". Deriving the scheme makes that drift unrepresentable. An
        explicit *_WS_URL env var still wins, for a deployment that terminates
        TLS somewhere unusual.
        """
        for svc in ("terminal_service", "automation_service"):
            if not getattr(self, f"{svc}_ws_url"):
                setattr(self, f"{svc}_ws_url", _ws_scheme(getattr(self, f"{svc}_url")))
        return self


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    require_secrets(settings, "service_jwt_secret")
    return settings
