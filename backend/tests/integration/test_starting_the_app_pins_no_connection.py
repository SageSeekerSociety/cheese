"""Starting the app keeps nothing alive that was alive before it started: a
database connection nobody holds any more is closed, whenever the app started.

A process can start the app more than once — the suite does for every test that
takes `client`. When each start froze the heap out of the collector, every
connection alive at that moment that was later dropped without being closed (a
pool disposed while its work was still running, a loop that ended under it)
stayed open to Postgres until the process exited, and a CI shard ran its
database out of connections.
"""

import asyncio
import gc
import time
import uuid

import asyncpg
import pytest

from tests.conftest import TEST_DATABASE_URL

_DSN = TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _sessions_named(name: str) -> int:
    conn = await asyncpg.connect(_DSN)
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE application_name = $1", name
        )
    finally:
        await conn.close()


@pytest.fixture
def dropped_later():
    """A connection opened before the app starts, on a loop that has already
    ended, so nothing can close it but its own collection."""
    import app.main  # noqa: F401 - the app is loaded, as in a serving process

    name = f"dropped-later-{uuid.uuid4().hex[:12]}"
    held: list = []

    async def open_one() -> None:
        held.append(
            await asyncpg.connect(_DSN, server_settings={"application_name": name})
        )

    asyncio.run(open_one())
    return name, held


def test_a_connection_dropped_after_the_app_started_is_closed(dropped_later, client):
    name, held = dropped_later
    assert asyncio.run(_sessions_named(name)) == 1

    held.clear()
    gc.collect()

    # The server sees the socket close a moment after the client closes it.
    deadline = time.monotonic() + 5
    while asyncio.run(_sessions_named(name)) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert asyncio.run(_sessions_named(name)) == 0
