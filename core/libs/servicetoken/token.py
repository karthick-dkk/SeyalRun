"""Short-lived HS256 JWTs for service-to-service calls.

api-gateway mints a token with ``iss=api-gateway`` and ``aud=<target
service>`` on every upstream request; identity-service/inventory-service
verify it (and the expected audience) before processing the request.
Tokens expire after ~60 seconds — minted fresh per request, never cached.
"""

from __future__ import annotations

import time

import jwt

DEFAULT_TTL_SECONDS = 60


class ServiceTokenError(Exception):
    pass


def mint(issuer: str, audience: str, secret: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    now = int(time.time())
    payload = {
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def verify(token: str, expected_audience: str, secret: str) -> dict:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"], audience=expected_audience)
    except jwt.PyJWTError as exc:
        raise ServiceTokenError(str(exc)) from exc
