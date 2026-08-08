"""Circuit breaker for calls to systems SeyalRun does not control.

Ported from the JumpServer integration's ``pravesh_shared.circuit_breaker``,
which is the one piece of that library with no core equivalent. Two changes on
the way in:

* Observability is injected rather than imported. The original wired itself
  directly to Prometheus counters; core reports flat JSON via
  ``libs.obsmetrics`` and logs through stdlib ``logging``, so a hard dependency
  would have dragged a second metrics model into core. Callers that want
  counters pass ``on_state_change``/``on_failure``.
* No httpx coupling — it wraps any coroutine, so it is equally usable for an
  external PAM's REST API, an SMTP hop, or a webhook delivery.

Keeping this stdlib-only matters: every other ``core/libs`` package is, which
is what lets the security-invariant suite run without a database, Redis, or
the service dependencies.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Any, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


class State(str, Enum):
    CLOSED = "closed"  # normal operation
    OPEN = "open"  # failing — reject immediately
    HALF_OPEN = "half_open"  # probing recovery


class CircuitOpenError(RuntimeError):
    """Raised when a call is rejected because the circuit is open."""


class CircuitBreaker:
    """Opens after ``failure_threshold`` consecutive failures, probes again
    after ``recovery_timeout`` seconds, and closes on the first success.

    One instance per external target, so a failing JumpServer cannot exhaust
    the connection budget that other integrations depend on.
    """

    def __init__(
        self,
        target: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        on_state_change: Callable[[str, State], None] | None = None,
        on_failure: Callable[[str, str], None] | None = None,
    ) -> None:
        self.target = target
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._on_state_change = on_state_change
        self._on_failure = on_failure

        self._state = State.CLOSED
        self._failure_count = 0
        self._last_failure_time: float | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> State:
        """Reports HALF_OPEN once the recovery window has elapsed.

        Derived rather than stored so no timer task is needed. (The ad-hoc
        breaker in the legacy ``jms_client`` instead spawned a sleeping
        ``asyncio.create_task`` per trip, which is what this replaces.)
        """
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
        """Run ``func`` through the breaker, or reject if the circuit is open."""
        async with self._lock:
            if self.state is State.OPEN:
                self._notify_failure("circuit_open")
                raise CircuitOpenError(
                    f"Circuit breaker OPEN for '{self.target}'. "
                    f"Retry in {self.recovery_timeout}s."
                )

        try:
            result = await func(*args, **kwargs)
        except Exception as exc:
            await self._record_failure(exc)
            raise
        await self._record_success()
        return result

    async def _record_success(self) -> None:
        async with self._lock:
            if self._state is not State.CLOSED:
                log.info("circuit_breaker_closed target=%s", self.target)
                self._notify_state(State.CLOSED)
            self._state = State.CLOSED
            self._failure_count = 0
            self._last_failure_time = None

    async def _record_failure(self, exc: Exception) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            self._notify_failure(type(exc).__name__)

            if self._failure_count >= self.failure_threshold:
                if self._state is not State.OPEN:
                    log.warning(
                        "circuit_breaker_opened target=%s failures=%d",
                        self.target,
                        self._failure_count,
                    )
                    self._notify_state(State.OPEN)
                self._state = State.OPEN
            else:
                log.info(
                    "circuit_breaker_failure target=%s failures=%d threshold=%d",
                    self.target,
                    self._failure_count,
                    self.failure_threshold,
                )

    def _notify_state(self, state: State) -> None:
        if self._on_state_change is not None:
            self._on_state_change(self.target, state)

    def _notify_failure(self, error_type: str) -> None:
        if self._on_failure is not None:
            self._on_failure(self.target, error_type)


__all__ = ["CircuitBreaker", "CircuitOpenError", "State"]
