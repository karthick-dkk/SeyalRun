from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from libs.secrets import require_secrets


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "terminal-service"
    host: str = "0.0.0.0"
    port: int = 8103
    log_level: str = "INFO"
    log_path: str = "/var/log/seyalrun/terminal-service.jsonl"

    db_engine: str = "postgres"
    db_host: str = "127.0.0.1"
    db_port: str = "5432"
    db_user: str = "seyalrun"
    db_password: str = ""
    db_sslmode: str = "require"
    terminal_db_name: str = "seyalrun_terminal"

    service_jwt_secret: str = ""

    # SSH host-key verification. Empty = no verification (logs a warning). Set to a
    # known_hosts file path to enforce strict host-key checking (mitigates MITM).
    ssh_known_hosts_path: str = ""

    identity_service_url: str = "http://identity-service:8101"
    inventory_service_url: str = "http://inventory-service:8102"
    recording_service_url: str = "http://recording-service:8104"

    redis_url: str = "redis://redis:6379/0"

    terminal_recording_max_frames: int = 20000
    terminal_policy_refresh_seconds: int = 60
    # How long to wait for the SSH server to reply to the shell/PTY request.
    # Some servers are slow to allocate a PTY; 30s is generous but bounded.
    ssh_shell_timeout: float = 30.0
    # Disconnect an interactive session after this many seconds with no client input.
    idle_timeout_seconds: int = 180
    # Mark sessions that were created but never had a WS connect as closed after this long.
    pending_reap_seconds: int = 120


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    require_secrets(settings, "db_password", "service_jwt_secret")
    return settings
