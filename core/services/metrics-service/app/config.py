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

    # Read-only connections to all four databases
    identity_db_name: str = "seyalrun_identity"
    inventory_db_name: str = "seyalrun_inventory"
    terminal_db_name: str = "seyalrun_terminal"
    automation_db_name: str = "seyalrun_automation"

    service_jwt_secret: str = ""
    identity_service_url: str = "http://identity-service:8101"

    weak_credential_threshold: int = 2

    # Dashboard payload is read-heavy and not real-time-critical; cache the
    # assembled result this long to avoid ~15 cross-DB queries on every load.
    dashboard_cache_ttl_seconds: int = 30

    log_level: str = "INFO"
    log_path: str = "/var/log/seyalrun/metrics-service.jsonl"

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    require_secrets(settings, "db_password", "service_jwt_secret")
    return settings
