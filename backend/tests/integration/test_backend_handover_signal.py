"""A rollout tells the backend traffic has just left to hand its running work
over (SIGUSR1), and that backend keeps answering what it still has while its
requests drain. Moved only when the old backend stopped, the work reached the
new one a drain later, and every new turn waited that long to start. A browser
watching a room through it is sent on with 1012.
"""

import asyncio
import os
import signal

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.ownership import OWNER_LOCK, Ownership
from app.core.ws_handover import EndBusinessSocketsAtHandover
from app.main import app


async def _owner_lock_holders() -> int:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            return await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_locks l"
                    " JOIN pg_database d ON d.oid = l.database"
                    " WHERE d.datname = current_database()"
                    " AND l.locktype = 'advisory' AND l.granted"
                    " AND l.classid = (CAST(:key AS bigint) >> 32)::oid"
                    " AND l.objid = (CAST(:key AS bigint) & 4294967295)::oid"
                    " AND l.objsubid = 1"
                ),
                {"key": OWNER_LOCK},
            )
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_a_backend_told_to_hand_over_lets_the_next_take_the_work_and_serves_on():
    incoming = Ownership(settings.database_url)
    try:
        async with app.router.lifespan_context(app):
            deadline = asyncio.get_running_loop().time() + 15
            while await _owner_lock_holders() != 1:
                assert asyncio.get_running_loop().time() < deadline, (
                    "never took the work"
                )
                await asyncio.sleep(0.1)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://backend"
            ) as client:
                before = (await client.get("/healthz")).status_code

                # A browser's room socket, held open through this process.
                sent: list[dict] = []
                accepted = asyncio.Event()

                async def room(scope, receive, send):
                    await send({"type": "websocket.accept"})
                    accepted.set()
                    while (await receive())["type"] != "websocket.disconnect":
                        pass

                async def browser_send(message):
                    sent.append(message)

                browser = asyncio.create_task(
                    EndBusinessSocketsAtHandover(room)(
                        {"type": "websocket", "path": "/topics/abc/chat"},
                        asyncio.Event().wait,
                        browser_send,
                    )
                )
                await asyncio.wait_for(accepted.wait(), timeout=5)

                # Its default action ends the process, so a backend that did not
                # catch it would not be handing anything over.
                assert signal.getsignal(signal.SIGUSR1) not in (signal.SIG_DFL, None)
                os.kill(os.getpid(), signal.SIGUSR1)

                await asyncio.wait_for(incoming.acquire(), timeout=30)
                assert incoming.held
                await asyncio.wait_for(browser, timeout=5)
                assert sent[-1] == {"type": "websocket.close", "code": 1012}
                assert (await client.get("/healthz")).status_code == before
    finally:
        await incoming.release()
