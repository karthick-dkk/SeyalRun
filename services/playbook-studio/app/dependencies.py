"""FastAPI dependencies — authentication via JumpServer token validation."""

from __future__ import annotations

import httpx
import redis.asyncio as aioredis
import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings

log = structlog.get_logger(__name__)
security = HTTPBearer()
settings = get_settings()

_redis: aioredis.Redis | None = None


def get_redis_client() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    redis: aioredis.Redis = Depends(get_redis_client),
) -> dict:
    """Validate Bearer token against JumpServer API. Caches result for 300s."""
    token = credentials.credentials
    cache_key = f"jms_token:{token[:16]}"

    # Check cache first
    cached = await redis.get(cache_key)
    if cached:
        import json

        return json.loads(cached)

    # Validate against JumpServer
    try:
        async with httpx.AsyncClient(verify=False, timeout=8) as client:
            r = await client.get(
                f"{settings.jumpserver_api_url}/api/v1/users/profile/",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code == 401:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or expired JumpServer token",
                )
            if r.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="JumpServer auth service unavailable",
                )
            user_data = r.json()
    except httpx.RequestError as exc:
        log.error("jms_auth_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cannot reach JumpServer for authentication",
        ) from exc

    # Cache the result
    import json

    user_info = {
        "id": str(user_data.get("id", "")),
        "username": user_data.get("username", ""),
        "name": user_data.get("name", ""),
        "email": user_data.get("email", ""),
        "token": token,
    }
    await redis.setex(cache_key, settings.token_cache_ttl_seconds, json.dumps(user_info))
    return user_info
