"""Fail-fast invariant: a service must refuse to start when a required secret
is unset or blank, instead of running with an empty signing key / disabled HMAC."""

from __future__ import annotations

import pytest

from libs.secrets import MissingSecretError, require_secrets


class _S:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_missing_secret_raises():
    s = _S(jwt_secret="", service_jwt_secret="set")
    with pytest.raises(MissingSecretError) as ei:
        require_secrets(s, "jwt_secret", "service_jwt_secret")
    assert "JWT_SECRET" in str(ei.value)


def test_whitespace_only_secret_raises():
    s = _S(api_token_pepper="   ")
    with pytest.raises(MissingSecretError):
        require_secrets(s, "api_token_pepper")


def test_all_set_passes():
    s = _S(jwt_secret="a", service_jwt_secret="b", db_password="c")
    require_secrets(s, "jwt_secret", "service_jwt_secret", "db_password")  # no raise


def test_reports_all_missing_at_once():
    s = _S(jwt_secret="", service_jwt_secret="", db_password="ok")
    with pytest.raises(MissingSecretError) as ei:
        require_secrets(s, "jwt_secret", "service_jwt_secret", "db_password")
    msg = str(ei.value)
    assert "JWT_SECRET" in msg and "SERVICE_JWT_SECRET" in msg and "DB_PASSWORD" not in msg
