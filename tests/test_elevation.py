"""PCI DSS Phase A: admin/superadmin JIT elevation window check
(libs/elevation.py), shared by terminal-service's SSH-connect gate and
inventory-service's credential-reveal gate. Pure function — no DB needed."""

from __future__ import annotations

import time

from libs.elevation import elevation_active


def test_future_timestamp_is_active():
    assert elevation_active(str(int(time.time()) + 600)) is True


def test_past_timestamp_is_not_active():
    assert elevation_active(str(int(time.time()) - 1)) is False


def test_expires_exactly_at_the_boundary():
    now = int(time.time())
    assert elevation_active(str(now)) is False  # "> now", not ">="
    assert elevation_active(str(now + 1)) is True


def test_empty_header_is_not_active():
    assert elevation_active("") is False


def test_non_numeric_header_is_not_active():
    # X-Elevated-Until is server-set, but this must fail closed regardless —
    # never trust a malformed/unexpected value as "elevated".
    assert elevation_active("not-a-number") is False
    assert elevation_active("123abc") is False
