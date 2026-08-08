"""Circuit breaker wrapping httpx calls to external APIs."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Any, TypeVar

from .log import get_logger
from .metrics import CIRCUIT_BREAKER_STATE, EXTERNAL_API_ERRORS

log = get_logger(__name__)

T = TypeVar("T")


class State(str, Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing — reject immediately
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreaker:
    """Async circuit breaker for external API calls.

    Opens after `failure_threshold` consecutive failures.
    Resets to HALF_OPEN after `recovery_timeout` seconds.
    Closes on first success in HALF_OPEN state.
    """

    def __init__(
        self,
        target: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ) -> None:
        self.target = target
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self._state = State.CLOSED
        self._failure_count = 0
        self._last_failure_time: float | None = None
        self._lock = asyncio.Lock()

        CIRCUIT_BREAKER_STATE.labels(target=target).set(0)

    @property
    def state(self) -> State:
        if (
            self._state == State.OPEN
            and self._last_failure_time is not None
            and time.monotonic() - self._last_failure_time >= self.recovery_timeout
        ):
            return State.HALF_OPEN
        return self._state

    async def call(
        self,
        func: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute func through the circuit breaker."""
        async with self._lock:
            current_state = self.state

            if current_state == State.OPEN:
                EXTERNAL_API_ERRORS.labels(target=self.target, error_type="circuit_open").inc()
                raise CircuitOpenError(
                    f"Circuit breaker OPEN for '{self.target}'. Retry in {self.recovery_timeout}s."
                )

        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except Exception as exc:
            await self._on_failure(exc)
            raise

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state in (State.HALF_OPEN, State.OPEN):
                log.info("circuit_breaker_closed", target=self.target)
            self._state = State.CLOSED
            self._failure_count = 0
            self._last_failure_time = None
            CIRCUIT_BREAKER_STATE.labels(target=self.target).set(0)

    async def _on_failure(self, exc: Exception) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            error_type = type(exc).__name__
            EXTERNAL_API_ERRORS.labels(target=self.target, error_type=error_type).inc()

            if self._failure_count >= self.failure_threshold:
                if self._state != State.OPEN:
                    log.warning(
                        "circuit_breaker_opened",
                        target=self.target,
                        failures=self._failure_count,
                    )
                self._state = State.OPEN
                CIRCUIT_BREAKER_STATE.labels(target=self.target).set(1)
            else:
                log.info(
                    "circuit_breaker_failure",
                    target=self.target,
                    failures=self._failure_count,
                    threshold=self.failure_threshold,
                )


class CircuitOpenError(RuntimeError):
    """Raised when a call is rejected because the circuit is open."""
