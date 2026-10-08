"""A rollout tells the backend traffic has just left to hand its running work
over (SIGUSR1), and that backend keeps answering what it still has while its
requests drain. Moved only when the old backend stopped, the work reached the
new one a drain later, and every new turn waited that long to start. A browser
watching a room through it is sent on with 1012.

The handover also has to keep what only this process is still holding: the
error bursts its `intake` collected on the request path (迁移顺序 2e). The
periodic job that would close those windows is stopped as part of handing over,
and the next process holds its own windows, never this one's.
"""

import asyncio
import os
import signal
import uuid

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.ownership import OWNER_LOCK, Ownership
from app.core.ws_handover import EndBusinessSocketsAtHandover
from app.domain import backend_log
from app.domain.run_record.models import RunRecord
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


@pytest.fixture(autouse=True)
def _fresh_intake(monkeypatch):
    """The intake singleton counts for the process's whole life, and these tests
    are not the only ones in it: each keeps its own windows."""
    monkeypatch.setattr(backend_log, "intake", backend_log.BackendErrorIntake())


async def _burst(headline: str, repeats: int = 5) -> None:
    """`repeats` identical failures into this process's intake, the way the
    middleware does for a request that raised: the window opens on the first and
    the rest are counted, so the flush has a summary carrying `repeats`."""
    for _ in range(repeats):
        await backend_log.report_request_failure(
            RuntimeError(headline), method="GET", path="/api/healthz"
        )


async def _burst_summaries(headline: str) -> list[dict]:
    """The summaries written for `headline`, read straight from the table — they
    are written at the handover, by which time this test has no client to ask."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session:
            rows = (
                await session.execute(
                    select(RunRecord.content, RunRecord.meta).where(
                        RunRecord.kind == "backend_error",
                        RunRecord.content.like(f"%{headline}%"),
                    )
                )
            ).all()
    finally:
        await engine.dispose()
    return [
        {"content": content, "meta": meta or {}}
        for content, meta in rows
        if (meta or {}).get("summary")
    ]


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


@pytest.mark.anyio
async def test_a_backend_handing_over_keeps_the_errors_it_is_still_holding():
    """Who keeps a burst is the process that collected it (迁移顺序 2e).

    A window younger than 300 s is one the `backend error flush` job would not
    have closed yet, and `hand_over()` stops that job on its way out. Nobody
    else has these failures: the next process starts with an intake of its own.

    The headline is fresh per run, so a record an earlier run left in this
    database is not mistaken for this one's.
    """
    headline = uuid.uuid4().hex
    async with app.router.lifespan_context(app):
        await _burst(headline)
        assert await _burst_summaries(headline) == []

        assert signal.getsignal(signal.SIGUSR1) not in (signal.SIG_DFL, None)
        os.kill(os.getpid(), signal.SIGUSR1)

        deadline = asyncio.get_running_loop().time() + 30
        while not await _burst_summaries(headline):
            assert asyncio.get_running_loop().time() < deadline, (
                "the handover never kept the window this process held"
            )
            await asyncio.sleep(0.1)

    [summary] = await _burst_summaries(headline)
    assert summary["meta"]["count"] == 5


@pytest.mark.anyio
async def test_a_backend_keeps_the_errors_it_collects_after_the_handover():
    """The drain after the handover keeps serving requests, so it can still open
    windows — and the job that would close them went with the lock. The last
    flush of this process's life is what keeps those (迁移顺序 2e)."""
    headline = uuid.uuid4().hex
    incoming = Ownership(settings.database_url)
    try:
        async with app.router.lifespan_context(app):
            os.kill(os.getpid(), signal.SIGUSR1)
            await asyncio.wait_for(incoming.acquire(), timeout=30)

            await _burst(headline)
            assert await _burst_summaries(headline) == []
        # This process is gone. Its windows could only have been kept by its own
        # last flush.
        [summary] = await _burst_summaries(headline)
        assert summary["meta"]["count"] == 5
    finally:
        await incoming.release()
