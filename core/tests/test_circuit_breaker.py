"""Circuit breaker invariants: a failing external PAM must stop consuming
resources quickly, and must be able to recover without a restart."""

from __future__ import annotations

import asyncio

import pytest

from libs.resilience import CircuitBreaker, CircuitOpenError, State


async def _ok() -> str:
    return "fine"


async def _boom() -> str:
    raise ConnectionError("upstream down")


def test_starts_closed_and_passes_calls_through():
    cb = CircuitBreaker("jumpserver")
    assert cb.state is State.CLOSED
    assert asyncio.run(cb.call(_ok)) == "fine"


def test_opens_only_after_threshold_is_reached():
    cb = CircuitBreaker("jumpserver", failure_threshold=3)

    async def drive():
        for _ in range(2):
            with pytest.raises(ConnectionError):
                await cb.call(_boom)
        # Still closed: two failures is under the threshold.
        assert cb.state is State.CLOSED
        with pytest.raises(ConnectionError):
            await cb.call(_boom)
        assert cb.state is State.OPEN

    asyncio.run(drive())


def test_open_circuit_rejects_without_calling_upstream():
    """The point of opening: stop hammering a system that is already down."""
    cb = CircuitBreaker("jumpserver", failure_threshold=1)
    calls = 0

    async def counting():
        nonlocal calls
        calls += 1
        raise ConnectionError("upstream down")

    async def drive():
        with pytest.raises(ConnectionError):
            await cb.call(counting)
        assert calls == 1
        # Rejected locally — upstream must not be touched again.
        with pytest.raises(CircuitOpenError):
            await cb.call(counting)
        assert calls == 1

    asyncio.run(drive())


def test_half_open_after_recovery_window_then_closes_on_success():
    cb = CircuitBreaker("jumpserver", failure_threshold=1, recovery_timeout=0.05)

    async def drive():
        with pytest.raises(ConnectionError):
            await cb.call(_boom)
        assert cb.state is State.OPEN

        await asyncio.sleep(0.06)
        assert cb.state is State.HALF_OPEN  # probe allowed

        assert await cb.call(_ok) == "fine"
        assert cb.state is State.CLOSED

    asyncio.run(drive())


def test_success_resets_the_failure_count():
    """Threshold counts *consecutive* failures; a success must clear the tally."""
    cb = CircuitBreaker("jumpserver", failure_threshold=2)

    async def drive():
        with pytest.raises(ConnectionError):
            await cb.call(_boom)
        await cb.call(_ok)
        with pytest.raises(ConnectionError):
            await cb.call(_boom)
        # Without the reset this second failure would have tripped the breaker.
        assert cb.state is State.CLOSED

    asyncio.run(drive())


def test_observability_hooks_are_optional_and_fire():
    states: list[tuple[str, State]] = []
    failures: list[tuple[str, str]] = []
    cb = CircuitBreaker(
        "jumpserver",
        failure_threshold=1,
        on_state_change=lambda t, s: states.append((t, s)),
        on_failure=lambda t, e: failures.append((t, e)),
    )

    async def drive():
        with pytest.raises(ConnectionError):
            await cb.call(_boom)

    asyncio.run(drive())
    assert ("jumpserver", State.OPEN) in states
    assert ("jumpserver", "ConnectionError") in failures


def test_a_raising_callback_is_not_counted_as_upstream_failure():
    """Regression: the original recorded success inside the try block, so a
    throwing success-handler was misattributed to the upstream call."""
    cb = CircuitBreaker("jumpserver", failure_threshold=1)

    async def drive():
        assert await cb.call(_ok) == "fine"
        assert cb.state is State.CLOSED

    asyncio.run(drive())
