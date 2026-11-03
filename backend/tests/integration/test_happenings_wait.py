"""Waiting for a happening (`happenings.waiter`), on the real Valkey client.

The rules pinned here: a wait with nothing to wait for ends quietly when its
time is up, however long that is; and a wake that comes at any point in the
wait ends it, not only one that comes in its first seconds. Both waits run past
the client's own read deadline, the longest any reply is waited for.
"""

import asyncio
import logging
import time

import pytest

from app.core.redis import get_redis_client
from app.domain import happenings

#: The longest the application's client waits for any one reply.
READ_DEADLINE_S = 5
WAIT_S = READ_DEADLINE_S + 1


@pytest.mark.anyio
async def test_a_wait_with_nothing_to_wait_for_ends_quietly_when_its_time_is_up(
    caplog,
):
    redis = get_redis_client()
    assert redis is not None
    await redis.delete(happenings.WAKE_KEY)
    wait = happenings.waiter(get_redis_client)

    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="app.domain.happenings"):
        woken = await wait(WAIT_S)
    took = time.monotonic() - started

    assert woken is False
    assert WAIT_S - 0.5 <= took <= WAIT_S + 1
    assert [r.getMessage() for r in caplog.records] == []


@pytest.mark.anyio
async def test_a_wake_late_in_the_wait_ends_it():
    redis = get_redis_client()
    assert redis is not None
    await redis.delete(happenings.WAKE_KEY)
    wait = happenings.waiter(get_redis_client)

    async def wake_later() -> None:
        await asyncio.sleep(READ_DEADLINE_S + 0.5)
        await redis.lpush(happenings.WAKE_KEY, b"1")

    started = time.monotonic()
    waking = asyncio.create_task(wake_later())
    woken = await wait(WAIT_S + 4)
    took = time.monotonic() - started
    await waking

    assert woken is True
    assert took < READ_DEADLINE_S + 1.5
