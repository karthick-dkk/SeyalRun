from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from libs.secrets import require_secrets


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "inventory-service"
    host: str = "0.0.0.0"
    port: int = 8102
    log_level: str = "INFO"
    log_path: str = "/var/log/seyalrun/inventory-service.jsonl"

    db_engine: str = "postgres"  # postgres|mysql
    db_host: str = "127.0.0.1"
    db_port: str = "5432"
    db_user: str = "seyalrun"
    db_password: str = ""
    db_sslmode: str = "require"
    inventory_db_name: str = "seyalrun_inventory"

    service_jwt_secret: str = ""

    za_vault_password: str = ""
    za_vault_salt: str = ""

    # PCI DSS Phase C — which KeyProvider plugin wraps each credential's per-row
    # DEK (see app/vault.py::encrypt_envelope, app/plugins/kms/). "env" is the
    # only built-in provider today; a future HSM/cloud-KMS plugin registers
    # under its own name and this just needs to point at it.
    kms_provider: str = "env"

    # Reveal token (Feature 6) — signed by identity-service with the shared JWT secret.
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"

    # Password strength (Feature 9) — zxcvbn score below this is "weak".
    weak_credential_threshold: int = 2
    # Rotation (Feature 10)
    rotation_default_days: int = 90

    identity_service_url: str = "http://identity-service:8101"
    automation_service_url: str = "http://automation-service:8105"

    zabbix_api_url: str = ""
    zabbix_api_token: str = ""


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    require_secrets(
        settings,
        "db_password",
        "service_jwt_secret",
        "jwt_secret",
        "za_vault_password",
        "za_vault_salt",
    )
    return settings
