"""Redis-backed sliding-window rate limiter, keyed by client-ip + user-id."""

from __future__ import annotations

import logging
import time

import redis.asyncio as redis

logger = logging.getLogger(__name__)


async def check_rate_limit(client: redis.Redis, key: str, limit: int, window_seconds: int) -> bool:
    """Returns True if the request is within the rate limit. Fails open if Redis is unreachable."""
    now = time.time()
    redis_key = f"ratelimit:{key}"
    try:
        async with client.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(redis_key, 0, now - window_seconds)
            pipe.zadd(redis_key, {f"{now:.6f}": now})
            pipe.zcard(redis_key)
            pipe.expire(redis_key, window_seconds)
            _, _, count, _ = await pipe.execute()
    except redis.RedisError:
        logger.warning("rate limiter: redis unavailable, failing open")
        return True
    return count <= limit
