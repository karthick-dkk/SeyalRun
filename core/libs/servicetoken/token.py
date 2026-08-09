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


def mint(
    issuer: str,
    audience: str,
    secret: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    subject: str | None = None,
) -> str:
    """Mint a short-lived internal service token.

    ``subject`` carries the end user the call is being made on behalf of. It
    matters because attribution written into the tamper-evident audit chain must
    be signed, not asserted: reading the actor from a plain X-User-Id header let
    any service-token holder attribute a credential fetch to any user, so the
    chain proved the row was unedited without proving it was ever true.
    """
    now = int(time.time())
    payload = {
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    if subject:
        payload["sub"] = subject
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
