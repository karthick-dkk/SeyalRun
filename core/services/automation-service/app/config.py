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
    identity_service_url: str = "http://identity-service:8101"
    inventory_service_url: str = "http://inventory-service:8102"
    recording_service_url: str = "http://recording-service:8104"
    redis_url: str = "redis://redis:6379/0"

    job_exec_timeout_seconds: int = 3600
    max_output_lines: int = 500

    # SSH host-key verification. Empty = no verification (logs a warning). Set to a
    # known_hosts file path to enforce strict host-key checking on outbound SSH
    # (mitigates MITM). See ops docs; full TOFU auto-pinning is a follow-up.
    ssh_known_hosts_path: str = ""

    # Housekeeping (Feature 3) — ES rollover target (optional; no-op if unset).
    es_url: str = ""
    es_index_prefix: str = "seyalrun"

    log_level: str = "INFO"
    log_path: str = "/var/log/seyalrun/automation-service.jsonl"

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    require_secrets(settings, "db_password", "service_jwt_secret")
    return settings
