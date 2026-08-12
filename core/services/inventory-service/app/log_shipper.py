"""Log shipper — the piece that was missing so 'no data uploaded'.

Every service writes structured JSON logs to the shared seyalrun_logs volume
(/var/log/seyalrun/<service>.jsonl). This background task tails those files and
ships new lines to the configured backend (Elasticsearch / S3 / both). It runs
inside inventory-service because that service owns the log-backend config, mounts
the logs volume, and ships boto3.

Design:
- Per-file byte offset persisted to /var/log/seyalrun/.ship-state.json so only NEW
  lines ship, and a restart resumes where it left off.
- On failure the offset is NOT advanced, so the batch retries next tick (at-least-once).
- File truncation/rotation (size < stored offset) resets that file's offset to 0.
- backend == local (or unset) => no-op.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from sqlalchemy import select

from libs.securelog.backends import ElasticsearchBackend, S3Backend

from .config import get_settings
from .database import SessionLocal
from .models import ZALogBackendConfig
from .vault import decrypt

logger = logging.getLogger(__name__)

LOG_DIR = "/var/log/seyalrun"
STATE_FILE = os.path.join(LOG_DIR, ".ship-state.json")
MAX_LINES_PER_FILE = 1000  # cap per tick so a huge backlog ships in chunks

CATEGORIES = ("app", "command", "audit", "recording")
# logger name (the top-level "logger" field) -> routing category
_LOGGER_CATEGORY = {"seyalrun.audit": "audit", "seyalrun.command": "command"}


def _classify(entry: dict) -> str:
    """Which routing category a log line belongs to. Audit/command events are emitted
    under dedicated logger names; everything else is general 'app' output."""
    return _LOGGER_CATEGORY.get(entry.get("logger", ""), "app")


def effective_routing(backend: str, routing: dict) -> dict:
    """category -> [backends]. An explicit routing matrix wins; when empty we derive
    it from the legacy single `backend` field so existing configs keep working."""
    if routing:
        return {c: list(routing.get(c) or []) for c in CATEGORIES}
    legacy = {"local": ["local"], "elasticsearch": ["elasticsearch"],
              "s3": ["s3"], "es+s3": ["elasticsearch", "s3"]}.get(backend, ["local"])
    return {c: legacy for c in CATEGORIES}


def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _save_state(state: dict) -> None:
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:  # noqa: BLE001
        logger.warning("log-shipper: could not persist offsets: %s", e)


async def _decrypted_config() -> tuple[str, dict] | None:
    async with SessionLocal() as session:
        cfg = (await session.execute(select(ZALogBackendConfig))).scalar_one_or_none()
        if cfg is None:
            return None

        def d(v: str) -> str:
            try:
                return decrypt(v) if v else ""
            except Exception:  # noqa: BLE001
                return ""
        return cfg.backend, {
            "es_url": cfg.es_url, "es_api_key": d(cfg.es_api_key),
            "es_index_prefix": cfg.es_index_prefix, "es_verify_ssl": cfg.es_verify_ssl,
            "s3_bucket": cfg.s3_bucket, "s3_region": cfg.s3_region,
            "s3_access_key_id": cfg.s3_access_key_id,
            "s3_secret_access_key": d(cfg.s3_secret_access_key),
            "s3_endpoint_url": cfg.s3_endpoint_url,
            "routing": cfg.routing or {},
        }


def _file_offsets(state: dict, fname: str, targets: list[str]) -> dict[str, int]:
    """Per-target byte offsets for a file, migrating the legacy `{fname: int}`
    single-offset format to `{fname: {target: int}}` so ES and S3 advance
    independently — a failing ES must not stall or duplicate S3 shipping."""
    raw = state.get(fname, {})
    if isinstance(raw, int):                       # legacy: one offset shared by all
        raw = {t: raw for t in targets}
    return {t: int(raw.get(t, 0)) for t in targets}


async def _ship_once() -> int:
    conf = await _decrypted_config()
    if conf is None:
        return 0
    backend_name, cfg = conf
    routing = effective_routing(backend_name, cfg.get("routing") or {})

    # Which named backends does any category use? Build each at most once and reuse.
    used = sorted({b for targets in routing.values() for b in targets if b != "local"})
    if not used:
        return 0   # everything routed to local only — nothing to ship
    backends = {}
    if "elasticsearch" in used:
        backends["elasticsearch"] = ElasticsearchBackend(
            cfg.get("es_url", ""), cfg.get("es_api_key", ""),
            cfg.get("es_index_prefix", "seyalrun"), verify_ssl=cfg.get("es_verify_ssl", True))
    if "s3" in used:
        backends["s3"] = S3Backend(cfg)
    # Categories each target is responsible for (inverse of the routing matrix).
    cats_for = {t: [c for c in CATEGORIES if t in routing.get(c, [])] for t in used}

    state = _load_state()
    shipped = 0
    for fname in sorted(os.listdir(LOG_DIR)):
        if not fname.endswith(".jsonl"):
            continue
        path = os.path.join(LOG_DIR, fname)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        offsets = _file_offsets(state, fname, used)

        # Each target ships from its own offset, so ES stalling never blocks or
        # re-sends S3 (and vice versa). At-least-once per target on failure.
        for target in used:
            b = backends.get(target)
            wanted = cats_for.get(target) or []
            if b is None or not wanted:
                continue
            offset = offsets[target]
            if offset > size:          # file rotated/truncated
                offset = 0
            if offset >= size:
                continue

            batch, new_offset, lines = [], offset, 0
            with open(path, "r", errors="ignore") as f:
                f.seek(offset)
                for line in f:
                    if not line.endswith("\n"):   # partial trailing line — retry next tick
                        break
                    new_offset += len(line.encode("utf-8", "ignore"))
                    s = line.strip()
                    if s:
                        try:
                            e = json.loads(s)
                            if _classify(e) in wanted:
                                batch.append(e)
                        except json.JSONDecodeError:
                            pass
                    lines += 1
                    if lines >= MAX_LINES_PER_FILE:
                        break

            try:
                if batch:
                    await b.write_batch(batch)
                    shipped += len(batch)
                offsets[target] = new_offset      # advance only on success
            except Exception as ex:  # noqa: BLE001
                logger.warning("log-shipper: %s -> %s failed (%d entries): %s — will retry",
                               fname, target, len(batch), ex)

        state[fname] = offsets

    _save_state(state)
    if shipped:
        logger.info("log-shipper: shipped %d entries (routing=%s)", shipped,
                    {c: routing[c] for c in CATEGORIES})
    return shipped


async def run_shipper() -> None:
    settings = get_settings()
    interval = getattr(settings, "log_ship_interval_seconds", 15)
    logger.info("log-shipper started (interval %ss)", interval)
    while True:
        try:
            await _ship_once()
        except Exception as e:  # noqa: BLE001
            logger.warning("log-shipper tick error: %s", e)
        await asyncio.sleep(interval)
