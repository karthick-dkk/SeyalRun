"""playbook-studio configuration — all settings via environment (PS_ prefix)."""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PS_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
    )

    service_name: str = "playbook-studio"
    log_level: str = "INFO"
    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8005

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://postgres:postgres@192.168.250.1:5432/playbook_studio"  # pragma: allowlist secret

    # Redis DB 5 (separate from sidecars on DB 1-4)
    redis_url: str = "redis://192.168.250.1:6379/5"
    job_ttl_seconds: int = 86400
    token_cache_ttl_seconds: int = 300

    # automation-bridge integration
    automation_bridge_url: str = "http://ab_service:8001"
    automation_bridge_timeout: int = 10

    # JumpServer auth validation
    jumpserver_api_url: str = "http://192.168.64.2"

    # SMTP for email alerts
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from_address: str = "alerts@seyalrun.local"
    smtp_use_tls: bool = True
    smtp_start_tls: bool = True

    # Webhook delivery
    webhook_timeout_seconds: int = 10
    webhook_max_retries: int = 3

    # Execution
    max_concurrent_jobs: int = 5
    rate_limit_per_minute: int = 30
    ws_output_buffer_size: int = 500

    # CORS
    cors_origins: list[str] = ["http://192.168.64.2:3000", "http://localhost:3000"]

    # Misc
    environment: str = "production"


_settings_instance: Settings | None = None


def get_settings() -> Settings:
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance
