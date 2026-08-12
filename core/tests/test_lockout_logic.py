"""T15 low-fix: after a lockout window expires, the counter must reset so a served
lockout doesn't re-lock on the very next failed attempt."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def lk(load_module):
    return load_module("services/identity-service/app/_lockout.py", "za_lockout")


def test_attempt_key_normalizes(lk):
    assert lk.attempt_key("ADMIN", "1.2.3.4") == "admin|1.2.3.4"
    assert lk.attempt_key("", "") == "|"


def test_reset_when_lock_expired(lk):
    # A prior lockout that has already elapsed -> fresh window (the actual bug fix).
    assert lk.should_reset(NOW - timedelta(seconds=1), NOW - timedelta(seconds=1), NOW, 900) is True


def test_no_reset_while_lock_active(lk):
    # Lock still in the future -> do not reset (would hand out extra attempts).
    assert lk.should_reset(NOW + timedelta(seconds=300), NOW, NOW, 900) is False


def test_reset_when_window_stale_no_lock(lk):
    assert lk.should_reset(None, NOW - timedelta(seconds=901), NOW, 900) is True


def test_no_reset_within_window_no_lock(lk):
    assert lk.should_reset(None, NOW - timedelta(seconds=10), NOW, 900) is False


def test_fresh_row_no_reset(lk):
    # updated_at None and no lock -> nothing to reset.
    assert lk.should_reset(None, None, NOW, 900) is False
