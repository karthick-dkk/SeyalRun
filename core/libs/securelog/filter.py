"""Structured JSON logging with secret redaction.

Every SeyalRun service calls :func:`configure_logging` at startup. Log
records are emitted as single-line JSON (consumed by zabbix-agent2's
``logrt[]`` items, see monitoring/), and any field/substring matching a
secret-like key — password, secret, token, vault, authorization — is
replaced with ``***REDACTED***`` before it ever reaches stdout.

Structured context is passed via Python's standard extra= kwarg:
  logger.info("msg", extra={"event_type": "acl_command_blocked", "session_id": "..."})

event_type and trace_id are promoted to top-level JSON fields; all other
extra keys appear under "extra": {...}.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone

_REDACT_KEYS = re.compile(r"(password|secret|token|vault|authorization|api[_-]?key|new_secret|private_key|passphrase)", re.IGNORECASE)
_REDACTED = "***REDACTED***"

# Standard LogRecord attributes — anything else was injected via extra= and belongs in our "extra" dict.
_STDLIB_ATTRS = frozenset({
    "args", "created", "exc_info", "exc_text", "filename", "funcName", "levelname",
    "levelno", "lineno", "message", "module", "msecs", "msg", "name", "pathname",
    "process", "processName", "relativeCreated", "stack_info", "taskName", "thread",
    "threadName",
})


def redact(value):
    """Recursively redact dict/list values whose key looks secret-bearing."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _REDACT_KEYS.search(str(k)):
                out[k] = _REDACTED
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            record.extra = redact(record.extra)
        if isinstance(record.args, dict):
            record.args = redact(record.args)
        msg = record.getMessage()
        for key in ("password", "secret", "token", "vault", "authorization", "private_key", "passphrase", "new_secret"):
            msg = re.sub(rf'({key}["\']?\s*[:=]\s*["\']?)([^"\'\s,}}]+)', rf"\1{_REDACTED}", msg, flags=re.IGNORECASE)
        record.msg = msg
        record.args = ()
        return True


class _JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # event_type and trace_id are promoted to top-level for easy log querying.
        event_type = getattr(record, "event_type", None)
        if event_type:
            payload["event_type"] = event_type
        trace_id = getattr(record, "trace_id", None)
        if trace_id:
            payload["trace_id"] = trace_id
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Collect all non-stdlib attributes injected via extra= into "extra" dict.
        ctx: dict = {}
        for key, val in record.__dict__.items():
            if key.startswith("_") or key in _STDLIB_ATTRS or key in ("event_type", "trace_id"):
                continue
            ctx[key] = val
        if ctx:
            payload["extra"] = redact(ctx)
        return json.dumps(payload)


def configure_logging(service: str, level: str = "INFO", log_path: str | None = None) -> None:
    """Configure root logger for ``service`` with JSON output + redaction.

    If ``log_path`` is given, logs are also appended there (for the shared
    ``seyalrun_logs`` volume that zabbix-agent2 tails).
    """
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()

    formatter = _JsonFormatter(service)
    redactor = _RedactingFilter()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(redactor)
    root.addHandler(stream_handler)

    if log_path:
        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redactor)
        root.addHandler(file_handler)
