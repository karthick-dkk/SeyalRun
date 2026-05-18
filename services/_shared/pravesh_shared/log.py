"""Structured JSON logging factory for all Pravesh services."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog


def configure_logging(service_name: str, log_level: str = "INFO") -> None:
    """Configure structlog for JSON output with service context."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    git_sha = os.getenv("GIT_SHA", "unknown")
    pod_name = os.getenv("POD_NAME", os.getenv("HOSTNAME", "unknown"))

    def inject_service_context(
        logger: Any, method: str, event_dict: dict[str, Any]
    ) -> dict[str, Any]:
        event_dict["service"] = service_name
        event_dict["git_sha"] = git_sha
        event_dict["pod"] = pod_name
        return event_dict

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_logger_name,
            inject_service_context,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
