"""playbook-studio configuration — all settings via environment (PS_ prefix)."""

from __future__ import annotations

import os

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
    # Path to a PEM CA bundle trusted when calling JumpServer over HTTPS.
    # Empty uses the system trust store. Verification is never disabled.
    jumpserver_ca_bundle: str = ""

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

    @property
    def jumpserver_verify(self) -> str | bool:
        """Value for httpx's ``verify`` when calling JumpServer."""
        bundle = self.jumpserver_ca_bundle.strip()
        if not bundle:
            return True
        if not os.path.isfile(bundle):
            raise RuntimeError(
                f"PS_JUMPSERVER_CA_BUNDLE points at '{bundle}', which is not a readable "
                "file. Provide a valid PEM CA bundle or unset it to use system CAs."
            )
        return bundle


_settings_instance: Settings | None = None


def get_settings() -> Settings:
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance
