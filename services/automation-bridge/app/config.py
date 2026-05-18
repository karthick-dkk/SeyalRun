"""automation-bridge configuration — all settings from env vars, never raw os.environ."""

from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AB_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
    )

    service_name: str = "automation-bridge"
    log_level: str = "INFO"
    host: str = "0.0.0.0"  # noqa: S104 — intentional; Nginx terminates externally
    port: int = 8001

    # Redis for job state storage
    redis_url: str = "redis://localhost:6379/0"
    job_ttl_seconds: int = 86400  # 24h

    # Playbooks
    playbooks_dir: Path = Path("/playbooks")
    approved_playbooks: list[str] = [
        "ping.yml",
        "patch-now.yml",
        "audit-sshd.yml",
        "freeipa-client-enroll.yml",
        "gather-facts.yml",
    ]

    # Rate limiting
    max_concurrent_jobs: int = 5
    rate_limit_per_minute: int = 20

    # JumpServer API (for callback/audit)
    jumpserver_api_url: str = "http://192.168.64.2"
    jumpserver_api_key: str = ""  # Loaded from secrets backend

    # Ansible runner settings
    ansible_runner_timeout: int = 300  # seconds

    @field_validator("approved_playbooks", mode="before")
    @classmethod
    def parse_playbook_list(cls, v: str | list) -> list[str]:
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v

    @field_validator("playbooks_dir")
    @classmethod
    def playbooks_dir_must_exist(cls, v: Path) -> Path:
        if not v.exists():
            v.mkdir(parents=True, exist_ok=True)
        return v


def get_settings() -> Settings:
    return Settings()
