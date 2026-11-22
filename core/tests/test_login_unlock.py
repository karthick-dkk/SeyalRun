"""Admin unlock of the login lockout: pure aggregation + route/RBAC/audit contract."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parent.parent
USERS_PY = (ROOT / "services/identity-service/app/api/users.py").read_text()


@pytest.fixture
def lk(load_module):
    return load_module("services/identity-service/app/_lockout.py", "za_lockout_unlock")


def test_aggregate_collapses_ips_and_skips_expired(lk):
    rows = [
        ("alice|10.0.0.1", 5, NOW + timedelta(seconds=600)),
        ("alice|10.0.0.2", 5, NOW + timedelta(seconds=120)),
        ("bob|10.0.0.1", 5, NOW - timedelta(seconds=1)),   # served
        ("carol|10.0.0.1", 3, None),                         # counting, not locked
    ]
    out = lk.aggregate_lockouts(rows, NOW)
    assert [e["username"] for e in out] == ["alice"]
    assert out[0]["remaining_seconds"] == 600
    assert out[0]["ips"] == ["10.0.0.1", "10.0.0.2"]


def test_prefix_matches_attempt_key(lk):
    assert lk.attempt_key("Alice", "1.2.3.4").startswith(lk.username_prefix("ALICE"))
    assert not lk.attempt_key("alice2", "1.2.3.4").startswith(lk.username_prefix("alice"))


def test_endpoints_admin_only_and_audited():
    for pat in (r'@router\.get\("/users/lockouts", dependencies=\[Depends\(require_admin\)\]\)',
                r'@router\.post\("/users/lockouts/\{username\}/unlock", dependencies=\[Depends\(require_admin\)\]\)'):
        assert re.search(pat, USERS_PY), pat
    body = USERS_PY.split("async def unlock_login_lockout", 1)[1].split("@router.", 1)[0]
    assert 'action="user.login_unlock"' in body
