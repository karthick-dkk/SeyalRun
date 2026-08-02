from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from libs.secrets import require_secrets


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "identity-service"
    host: str = "0.0.0.0"
    port: int = 8101
    log_level: str = "INFO"
    log_path: str = "/var/log/seyalrun/identity-service.jsonl"

    db_engine: str = "postgres"  # postgres|mysql
    db_host: str = "127.0.0.1"
    db_port: str = "5432"
    db_user: str = "seyalrun"
    db_password: str = ""
    db_sslmode: str = "require"
    identity_db_name: str = "seyalrun_identity"

    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"

    # Server-side session store (Redis) — sets the initial idle-window TTL at
    # login; api-gateway (which owns session lookup on every request) is the
    # one that slides it forward and enforces the absolute cap.
    redis_url: str = "redis://redis:6379/0"
    session_idle_minutes: int = 30

    service_jwt_secret: str = ""
    api_token_pepper: str = ""

    # Vault — shared with inventory-service; used to encrypt TOTP secrets (Feature 6).
    za_vault_password: str = ""
    za_vault_salt: str = ""

    zabbix_api_url: str = ""
    zabbix_api_token: str = ""

    # Zabbix module trust anchor — HMAC-signs the Zabbix->SeyalRun module SSO
    # handshake (zbx-sso-init). Mirrors zabbix_webhook_hmac_secret's role: the
    # one thing that lets the module assert "this Zabbix user, this privilege
    # level" without a prior SeyalRun session. Optional — only required if the
    # Zabbix module is actually deployed; left blank, /auth/zbx-sso-init 503s.
    zabbix_module_secret: str = ""

    # Used only to resolve a Zabbix hostid -> SeyalRun host_id for a kiosk login
    # (see auth.py::_resolve_kiosk_host); never used for anything user-facing.
    inventory_service_url: str = "http://inventory-service:8102"

    # PCI DSS Phase B — security-event alerts (login spikes, etc.) fire through
    # automation-service's notification pipeline (see app/_alerts.py) rather than
    # identity-service growing its own in-app notification/email plumbing.
    automation_service_url: str = "http://automation-service:8105"

    # PCI DSS Phase C — deprovisioning webhook kills every active session for the
    # deactivated user immediately (see api/users.py::deprovision_user).
    terminal_service_url: str = "http://terminal-service:8103"

    frontend_origin: str = ""

    audit_log_retention_days: int = 180

    seed_admin_username: str = "Admin"

    # Login brute-force lockout (DB-backed; keyed by username+IP).
    login_max_failures: int = 5
    login_lockout_seconds: int = 900
    login_attempt_window_seconds: int = 900
    # PCI DSS Phase B — distinct accounts failing from one IP within the same
    # window before a login-spike security alert fires (see _lockout.py).
    login_spike_threshold: int = 3


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    require_secrets(
        settings,
        "db_password",
        "jwt_secret",
        "service_jwt_secret",
        "api_token_pepper",
        "za_vault_password",
        "za_vault_salt",
    )
    return settings
