"""The /users/lookup budget must not take invitations down with the brake it
relies on.

Redis only brakes the lookup: a Redis that accepts the connection and stops
answering has to let the lookup through within the bound the rest of the
request path gives it, and a counter whose expiry was lost must not keep
refusing the account after the window is over.
"""

import asyncio
import time

import pytest

from app.api.routes.users import directory
from app.core.config import settings


class _HungRedis:
    async def incr(self, key: str) -> int:
        await asyncio.Event().wait()
        return 1

    async def expire(self, key: str, seconds: int, *, nx: bool = False) -> bool:
        return True


class _LostExpiryRedis:
    """A counter whose first EXPIRE dies on the wire after its INCR landed."""

    def __init__(self) -> None:
        self.count = 0
        self.ttls: dict[str, int] = {}
        self.lose_next_expiry = True

    async def incr(self, key: str) -> int:
        self.count += 1
        return self.count

    async def expire(self, key: str, seconds: int, *, nx: bool = False) -> bool:
        if self.lose_next_expiry:
            self.lose_next_expiry = False
            raise ConnectionError("connection reset between the two commands")
        if nx and key in self.ttls:
            return False
        self.ttls[key] = seconds
        return True


async def test_a_hung_redis_lets_the_lookup_through(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(directory, "get_redis_client", lambda: _HungRedis())
    monkeypatch.setattr(settings, "request_limit_redis_timeout_s", 0.05)

    started = time.monotonic()
    await asyncio.wait_for(directory._spend_a_lookup(7), timeout=2)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, elapsed


async def test_a_lost_expiry_is_set_again_on_the_next_lookup(
    monkeypatch: pytest.MonkeyPatch,
):
    redis = _LostExpiryRedis()
    monkeypatch.setattr(directory, "get_redis_client", lambda: redis)

    await directory._spend_a_lookup(7)  # INCR lands, its EXPIRE is lost
    await directory._spend_a_lookup(7)  # heals the key instead of refusing it

    assert redis.ttls == {directory._LOOKUP_KEY.format(7): directory.LOOKUP_WINDOW_S}
