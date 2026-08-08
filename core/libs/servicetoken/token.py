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
    # HS256 is deliberate here, not an oversight. The rule guards against
    # symmetric signing where a third party must verify independently — that
    # would mean handing out the shared secret. These tokens never leave the
    # internal trust domain: the same deployment mints and verifies them with a
    # secret it already holds, and they expire in ~60s. RS256 would add key
    # distribution and rotation for no gain. Any token an external system must
    # verify (e.g. a PAM launch handoff) does need RS256 and must not use this.
    return jwt.encode(payload, secret, algorithm="HS256")  # nosemgrep: jwt-must-use-rs256


def verify(token: str, expected_audience: str, secret: str) -> dict:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"], audience=expected_audience)
    except jwt.PyJWTError as exc:
        raise ServiceTokenError(str(exc)) from exc
