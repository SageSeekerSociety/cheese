"""Lock waiters leave the request pool within a bound, without changing migrations."""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import db
from app.core.errors import register_exception_handlers
from app.domain.project.models import Project
from app.domain.team.models import Team
from app.domain.topic.models import Topic
from tests.conftest import TEST_DATABASE_URL
from tests.integration.conftest import a_team


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("configured", "expected"), [(None, "15s"), ("750ms", "750ms")]
)
async def test_each_request_transaction_has_its_timeout_but_background_does_not(
    production_app_engine, monkeypatch, configured, expected
):
    if configured is None:
        monkeypatch.delenv("DB_REQUEST_LOCK_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("DB_REQUEST_LOCK_TIMEOUT", configured)
    request = asynccontextmanager(db.get_db)
    async with request() as session:
        assert await session.scalar(text("SHOW lock_timeout")) == expected
        await session.commit()
        assert await session.scalar(text("SHOW lock_timeout")) == expected
        async with session.begin_nested() as savepoint:
            assert await session.scalar(text("SHOW lock_timeout")) == expected
            await savepoint.rollback()
        assert await session.scalar(text("SHOW lock_timeout")) == expected
        await db.release_read_session(session)
        assert await session.scalar(text("SHOW lock_timeout")) == expected

    async with db.async_session_factory() as background:
        # SET LOCAL must not leak through the pooled connection into other work.
        assert await background.scalar(text("SHOW lock_timeout")) == "0"


@pytest.mark.anyio
async def test_a_room_lock_wait_returns_503_within_twenty_seconds_and_releases_the_pool(
    production_app_engine, monkeypatch
):
    monkeypatch.delenv("DB_REQUEST_LOCK_TIMEOUT", raising=False)
    # One request slot makes a leaked/failed transaction observable immediately.
    request_engine = create_async_engine(
        TEST_DATABASE_URL, pool_size=1, max_overflow=0, pool_timeout=1
    )
    holder_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    sessions = async_sessionmaker(request_engine, expire_on_commit=False)
    monkeypatch.setattr(db, "async_session_factory", sessions)
    project_id, room_id = uuid.uuid4(), uuid.uuid4()
    team_id = None
    test_app = FastAPI()
    register_exception_handlers(test_app)

    @test_app.get("/lock")
    async def lock(session: AsyncSession = Depends(db.get_db)):
        await session.execute(
            text("SELECT id FROM topics WHERE id = :id FOR KEY SHARE"),
            {"id": room_id},
        )
        return {"locked": True}

    @test_app.get("/read")
    async def read(session: AsyncSession = Depends(db.get_db)):
        return {"value": await session.scalar(text("SELECT 1"))}

    try:
        async with sessions() as seed:
            team_id = await a_team(seed)
            seed.add(Project(id=project_id, name="Lock timeout", team_id=team_id))
            await seed.flush()
            seed.add(Topic(id=room_id, project_id=project_id, title="Locked room"))
            await seed.commit()
        async with holder_engine.begin() as holder:
            # KEY SHARE readers coexist; FOR UPDATE is the incompatible holder.
            await holder.execute(
                text("SELECT id FROM topics WHERE id = :id FOR UPDATE"),
                {"id": room_id},
            )
            async with AsyncClient(
                transport=ASGITransport(app=test_app), base_url="http://test"
            ) as http:
                started = time.monotonic()
                response = await asyncio.wait_for(http.get("/lock"), timeout=20)
                elapsed = time.monotonic() - started
                assert response.status_code == 503, response.text
                assert 14 <= elapsed < 20, elapsed
                assert int(response.headers["Retry-After"]) > 0
                assert response.json()["code"] == 503
                assert db.pool_status(request_engine)["checked_out"] == 0
                # The holder is STILL locked; unrelated work can use the slot.
                other = await asyncio.wait_for(http.get("/read"), timeout=1)
                assert other.status_code == 200, other.text
                assert other.json() == {"value": 1}
                await holder.rollback()
                retry = await asyncio.wait_for(http.get("/lock"), timeout=1)
                assert retry.status_code == 200, retry.text
                assert retry.json() == {"locked": True}
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(delete(Project).where(Project.id == project_id))
            if team_id is not None:
                await cleanup.execute(delete(Team).where(Team.id == team_id))
            await cleanup.commit()
        await request_engine.dispose()
        await holder_engine.dispose()
