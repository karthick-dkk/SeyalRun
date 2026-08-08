from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from libs.secrets import require_secrets


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
    terminal_service_ws_url: str = "ws://terminal-service:8103"
    recording_service_url: str = "http://recording-service:8104"
    automation_service_url: str = "http://automation-service:8105"
    automation_service_ws_url: str = "ws://automation-service:8105"
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


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    require_secrets(settings, "service_jwt_secret")
    return settings
