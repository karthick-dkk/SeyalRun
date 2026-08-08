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
    terminal_db_name: str = "seyalrun_terminal"

    service_jwt_secret: str = ""
    identity_service_url: str = "http://identity-service:8101"
    terminal_service_url: str = "http://terminal-service:8103"
    inventory_service_url: str = "http://inventory-service:8102"

    recording_retention_days: int = 90

    # Tiering (Feature 4) — move local recordings to S3 after N days; purge after retention.
    recording_tier_after_days: int = 7
    s3_bucket: str = ""
    s3_region: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_endpoint_url: str = ""

    log_level: str = "INFO"
    log_path: str = "/var/log/seyalrun/recording-service.jsonl"

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    require_secrets(settings, "db_password", "service_jwt_secret")
    return settings
