from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings

from libs.secrets import require_secrets


class Settings(BaseSettings):
    db_engine: str = "postgres"
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "seyalrun"
    db_password: str = ""
    db_sslmode: str = "require"
    automation_db_name: str = "seyalrun_automation"

    service_jwt_secret: str = ""
    automation_service_url: str = "http://automation-service:8105"
    inventory_service_url: str = "http://inventory-service:8102"
    identity_service_url: str = "http://identity-service:8101"
    redis_url: str = "redis://redis:6379/0"

    zabbix_api_url: str = ""
    zabbix_api_token: str = ""
    zabbix_webhook_hmac_secret: str = ""
    zabbix_console_url: str = ""
    job_exec_timeout_seconds: int = 3600

    # Webhook hardening (the /webhook/ route bypasses the gateway, so it has no
    # gateway rate-limit). Replay window rejects a re-sent identical signed body.
    webhook_rate_limit_per_minute: int = 120
    webhook_replay_window_seconds: int = 300
    zabbix_webhook_ip_allowlist: str = ""  # comma-separated source IPs; empty = allow all

    log_level: str = "INFO"
    log_path: str = "/var/log/seyalrun/zabbix-integration-service.jsonl"

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    require_secrets(
        settings, "db_password", "service_jwt_secret", "zabbix_webhook_hmac_secret"
    )
    return settings
