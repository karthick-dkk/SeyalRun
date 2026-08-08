"""launch-token — mint short-lived, one-use tokens for Zabbix→JumpServer session launch.

Tokens are JWT-shaped but signed with HS256, not RS256: SeyalRun both mints and
verifies them, so a shared secret is sufficient and no third party needs to
verify independently. Moving to RS256 only becomes necessary if JumpServer is
ever expected to verify these tokens itself.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import redis.asyncio as aioredis
import structlog
from fastapi import Depends, FastAPI, HTTPException, status
from pravesh_shared.health import register_readiness_check
from pravesh_shared.health import router as health_router
from pravesh_shared.log import configure_logging
from pravesh_shared.middleware import RequestContextMiddleware
from prometheus_client import make_asgi_app
from pydantic import BaseModel, ConfigDict, Field

configure_logging("launch-token", os.getenv("LT_LOG_LEVEL", "INFO"))
log = structlog.get_logger(__name__)

REDIS_URL = os.getenv("LT_REDIS_URL", "redis://localhost:6379/3")
TOKEN_TTL = int(os.getenv("LT_JWT_TOKEN_TTL_SECONDS", "60"))
JUMPSERVER_URL = os.getenv("LT_JUMPSERVER_URL", "https://192.168.64.2")

TOKEN_SECRET = os.getenv("LT_TOKEN_SECRET", "")
# This placeholder was this service's default until the Phase 0 hardening pass,
# so it is public in git history. It is 41 characters and would otherwise pass
# the length check, hence the explicit denylist.
_REJECTED_SECRETS = {"change-me-in-production-use-strong-secret"}
if len(TOKEN_SECRET) < 32 or TOKEN_SECRET in _REJECTED_SECRETS:
    raise RuntimeError(
        "LT_TOKEN_SECRET is required, must be at least 32 characters, and must not "
        "be a known placeholder. It signs session-launch tokens, so a guessable "
        "value lets an attacker mint a token for any user against any asset."
    )

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis not initialized")
    return _redis


async def check_redis() -> bool:
    r = get_redis()
    await r.ping()
    return True


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _redis
    _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    register_readiness_check("redis", check_redis)
    log.info("launch_token_starting", ttl=TOKEN_TTL)
    yield
    await _redis.aclose()


app = FastAPI(title="launch-token", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
app.include_router(health_router)
app.mount("/metrics", make_asgi_app())


class LaunchTokenRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    zabbix_user: Annotated[str, Field(max_length=100)]
    asset_id: Annotated[str, Field(max_length=50)]
    asset_name: Annotated[str, Field(max_length=200)]


class LaunchTokenResponse(BaseModel):
    token: str
    expires_in: int
    jumpserver_url: str
    asset_id: str


@app.post("/launch-tokens", response_model=LaunchTokenResponse)
async def create_launch_token(
    req: LaunchTokenRequest,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> LaunchTokenResponse:
    """Mint a one-use, 60-second launch token for Zabbix→JumpServer sessions."""
    jti = str(uuid.uuid4())
    issued_at = int(time.time())
    expires_at = issued_at + TOKEN_TTL

    import base64
    import hmac
    import json

    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        .rstrip(b"=")
        .decode()
    )
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "sub": req.zabbix_user,
                    "asset_id": req.asset_id,
                    "jti": jti,
                    "iat": issued_at,
                    "exp": expires_at,
                }
            ).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    signing_input = f"{header}.{payload}"
    sig = (
        base64.urlsafe_b64encode(
            hmac.new(TOKEN_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    token = f"{signing_input}.{sig}"

    # Store JTI in Redis for one-use enforcement (TTL = token TTL + 10s grace)
    await redis.setex(f"launch_token:{jti}", TOKEN_TTL + 10, "pending")

    log.info(
        "token_minted",
        jti=jti,
        user=req.zabbix_user,
        asset_id=req.asset_id,
        expires_at=expires_at,
    )

    return LaunchTokenResponse(
        token=token,
        expires_in=TOKEN_TTL,
        jumpserver_url=f"{JUMPSERVER_URL}/?token={token}&asset={req.asset_id}",
        asset_id=req.asset_id,
    )


@app.post("/launch-tokens/verify")
async def verify_token(
    token: str,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> dict:
    """Verify and consume a launch token (signature + expiry + one-use)."""
    import base64
    import hmac
    import json

    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token format"
        ) from None

    # Check the signature BEFORE decoding the payload. Until this passes, every
    # field in the token is attacker-controlled: `sub` and `asset_id` decide who
    # gets a session on which host, so trusting them unverified would let anyone
    # holding one token rewrite it into a session as any user on any asset.
    expected_sig = (
        base64.urlsafe_b64encode(
            hmac.new(
                TOKEN_SECRET.encode(),
                f"{header_b64}.{payload_b64}".encode(),
                hashlib.sha256,
            ).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    if not hmac.compare_digest(expected_sig, sig_b64):
        log.warning("token_bad_signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token signature"
        )

    try:
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token format"
        ) from None

    jti = payload.get("jti", "")
    exp = payload.get("exp", 0)

    if int(time.time()) > exp:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")

    # One-use check via Redis
    result = await redis.getset(f"launch_token:{jti}", "used")
    if result == "used":
        log.warning("token_replay_detected", jti=jti)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token already used")
    if result is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token not found")

    log.info("token_verified", jti=jti, user=payload.get("sub"))
    return {
        "valid": True,
        "user": payload.get("sub"),
        "asset_id": payload.get("asset_id"),
    }
