from functools import lru_cache

from redis.asyncio import Redis, from_url

from app.core.config import settings

# Returns one unit to a counter a rate-limit script took it from, when the
# thing it was taken for never happened. A counter that has already expired
# stays gone rather than coming back at -1.
GIVE_BACK_ONE_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  redis.call('DECR', KEYS[1])
end
return 1
"""


@lru_cache(maxsize=1)
def get_redis_client() -> Redis | None:
    """Return a lazily initialized asyncio Redis client.

    The underlying connection is established on-demand when the first
    command is awaited. Returning ``None`` allows callers to gracefully
    degrade when Redis is not configured (e.g., tests without Redis).
    """

    redis_url = settings.redis_url
    if not redis_url:
        return None
    return from_url(redis_url, encoding="utf-8", decode_responses=False)
