"""A test that reads through the app pool on a loop of its own does not break
the test after it.

The first test leaves a connection bound to its own, now closed, loop in the
application pool. The second borrows from that pool on the session portal, the
way a script's ``main`` does. Run in this order on one worker (serially, or
when the scheduler happens to keep them together), the second failed with
"attached to a different loop" and "Event loop is closed".
"""

import asyncio

from anyio.from_thread import BlockingPortal
from sqlalchemy import text

from app.core.db import async_session_factory


async def _read() -> int:
    async with async_session_factory() as session:
        return int(await session.scalar(text("select 1")))


def test_a_one_off_loop_reads_through_the_app_pool(_pg_schema) -> None:
    assert asyncio.run(_read()) == 1


def test_the_portal_reads_through_the_app_pool_after_it(
    _portal: BlockingPortal,
) -> None:
    assert _portal.call(_read) == 1
