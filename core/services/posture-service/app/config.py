from __future__ import annotations

from functools import lru_cache

from libs.secrets import require_secrets
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_engine: str = "postgres"
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "seyalrun"
    db_password: str = ""
    db_sslmode: str = "require"
    posture_db_name: str = "seyalrun_posture"

    service_jwt_secret: str = ""
    identity_service_url: str = "http://identity-service:8101"
    inventory_service_url: str = "http://inventory-service:8102"
    automation_service_url: str = "http://automation-service:8105"

    # Trusted Ed25519 public keys for offline feed bundles. More than one so a
    # signing key can be rotated without a flag day. Format, comma- or
    # newline-separated: "<name>:<base64 raw 32-byte public key>". The file
    # variant (same format, one per line, '#' comments) is for keys mounted
    # from outside the image.
    posture_feed_trusted_keys: str = ""
    posture_feed_trusted_keys_file: str = ""
    # Upper bounds that keep an import from exhausting a small box: the upload
    # itself, everything it unpacks to, and the member count.
    posture_feed_max_bytes: int = 256 * 1024 * 1024
    posture_feed_max_unpacked_bytes: int = 4 * 1024 * 1024 * 1024
    posture_feed_max_members: int = 20000
    # Unpack/staging area. On a disk volume, NOT /tmp — /tmp is a tmpfs (RAM)
    # in the hardened compose files.
    posture_data_dir: str = "/var/lib/seyalrun/posture"

    # Facts are "stale" after this long without a fresh collection.
    posture_stale_hours: int = 24
    # How often outstanding facts runs are polled, and when one is given up on.
    posture_poll_interval_seconds: int = 5
    posture_collect_timeout_minutes: int = 60

    # M2 findings. The feed is reported stale (and the Findings page warns)
    # once the active snapshot was generated more than this many days ago.
    posture_feed_stale_days: int = 7
    # Upper bounds for one imported scanner result file.
    posture_scan_max_bytes: int = 64 * 1024 * 1024
    posture_scan_max_records: int = 500000

    log_level: str = "INFO"
    log_path: str = "/var/log/seyalrun/posture-service.jsonl"

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    require_secrets(settings, "db_password", "service_jwt_secret")
    return settings
