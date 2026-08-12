"""Secret-redaction invariant: secret-bearing keys never reach the log sink."""

from __future__ import annotations

import pytest


@pytest.fixture
def securelog(load_module):
    return load_module("libs/securelog/filter.py", "za_securelog")


def test_redacts_secret_keys(securelog):
    out = securelog.redact({"username": "alice", "password": "hunter2", "api_key": "x"})
    assert out["username"] == "alice"
    assert out["password"] == securelog._REDACTED
    assert out["api_key"] == securelog._REDACTED


def test_redacts_nested(securelog):
    # "items" is a non-secret container key, so we recurse into it; the inner
    # "token" key is what gets redacted.
    out = securelog.redact({"cfg": {"db_password": "p", "host": "h"}, "items": [{"token": "t"}]})
    assert out["cfg"]["db_password"] == securelog._REDACTED
    assert out["cfg"]["host"] == "h"
    assert out["items"][0]["token"] == securelog._REDACTED


def test_secret_bearing_container_key_redacts_whole_value(securelog):
    # A key that itself matches (e.g. "tokens" contains "token") redacts its entire value.
    out = securelog.redact({"tokens": [{"token": "t"}]})
    assert out["tokens"] == securelog._REDACTED
