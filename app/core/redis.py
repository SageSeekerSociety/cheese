from functools import lru_cache
from typing import Optional

from redis.asyncio import Redis, from_url

from app.core.config import settings


@lru_cache(maxsize=1)
def get_redis_client() -> Optional[Redis]:
    """Return a lazily initialized asyncio Redis client.

    The underlying connection is established on-demand when the first
    command is awaited. Returning ``None`` allows callers to gracefully
    degrade when Redis is not configured (e.g., tests without Redis).
    """

    redis_url = settings.redis_url
    if not redis_url:
        return None
    return from_url(redis_url, encoding="utf-8", decode_responses=False)
