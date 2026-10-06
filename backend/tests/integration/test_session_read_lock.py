"""Tool calls of one session read its row side by side.

Every tool call checks the session's lease before it reaches the machine
(``api/routes/execution.py``), inside the transaction that records the
dispatch. A file-system walk sends a burst of calls for the same session at
once. If each reader takes the row exclusively, the burst queues on that one
row, and every queued call holds a database connection while it waits, which
fills the connection owner's pool. A lease change must still wait for the
calls that checked the old lease to commit.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.agent_session.models import AgentSession
from app.domain.device import owner_reads
from tests.conftest import TEST_DATABASE_URL
from tests.integration.conftest import new_project, session_auth_headers


def _session(client) -> uuid.UUID:
    project = new_project(client, "Burst", owner="alice")
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    generation = str(uuid.uuid4())
    session_id = uuid.uuid4()

    async def seed() -> None:
        async with client.test_request_factory() as db:
            db.add(
                AgentSession(
                    id=session_id,
                    conversation_id=uuid.UUID(room),
                    agent_handle="agent",
                    harness="claude-code",
                    work_lease={
                        "kind": "device",
                        "generation": generation,
                        "resource_id": generation,
                        "device_id": "machine-1",
                        "status": "ready",
                    },
                )
            )
            await db.commit()

    client.portal.call(seed)
    return session_id


def test_calls_of_one_session_check_its_lease_side_by_side(client):
    session_id = _session(client)

    async def scenario() -> None:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as first, sessions() as second:
                assert await owner_reads.session_execution(first, session_id)
                # The first call has not committed yet.
                await second.execute(text("SET LOCAL lock_timeout = '2s'"))
                try:
                    assert await owner_reads.session_execution(second, session_id)
                except DBAPIError as exc:
                    pytest.fail(f"the second call waited on the first: {exc}")
                await second.commit()
                await first.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_a_lease_change_waits_for_the_call_that_checked_it(client):
    session_id = _session(client)

    async def scenario() -> None:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as call, sessions() as lease:
                assert await owner_reads.session_execution(call, session_id)
                await lease.execute(text("SET LOCAL lock_timeout = '500ms'"))
                with pytest.raises(DBAPIError, match="lock timeout"):
                    await lease.execute(
                        update(AgentSession)
                        .where(AgentSession.id == session_id)
                        .values(work_lease=None)
                    )
                await lease.rollback()
                await call.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
