"""The pool gauge says what is in use, and saturation is said out loud once."""

import logging

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import QueuePool

from app.core.config import settings
from app.core.db import get_db, pool_status, warn_when_pool_saturates
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.anyio


def _assert_production_pool(engine) -> None:
    pool = engine.pool
    assert isinstance(pool, QueuePool)
    assert pool.size() == settings.db_pool_size
    assert pool._max_overflow == settings.db_max_overflow


async def test_shared_application_engine_serves_requests_from_the_production_pool(
    production_app_engine,
):
    _assert_production_pool(production_app_engine)
    checkouts = []

    @event.listens_for(production_app_engine.sync_engine, "checkout")
    def _checked_out(*_args):
        checkouts.append(True)

    test_app = FastAPI()

    @test_app.get("/read")
    async def read(session: AsyncSession = Depends(get_db)):
        return {"value": await session.scalar(text("SELECT 1"))}

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as test_client:
        response = await test_client.get("/read")

    assert response.json() == {"value": 1}
    assert checkouts


async def test_sync_client_requests_use_the_production_pool(client):
    engine = client.test_app_engine
    _assert_production_pool(engine)
    checkouts = []

    @event.listens_for(engine.sync_engine, "checkout")
    def _checked_out(*_args):
        checkouts.append(True)

    assert client.get("/projects?team_id=999999").status_code == 200
    assert checkouts


async def test_async_integration_fixtures_use_the_production_pool(
    db_factory, python_client
):
    fixture_engine = db_factory.kw["bind"]
    request_engine = python_client.test_app_engine
    _assert_production_pool(fixture_engine)
    _assert_production_pool(request_engine)

    async with db_factory() as session:
        assert await session.scalar(text("SELECT 1")) == 1

    checkouts = []

    @event.listens_for(request_engine.sync_engine, "checkout")
    def _checked_out(*_args):
        checkouts.append(True)

    assert (await python_client.get("/projects?team_id=999999")).status_code == 200
    assert checkouts


async def test_gauge_counts_checked_out_connections_and_saturation_is_logged(
    caplog,
):
    engine = create_async_engine(TEST_DATABASE_URL, pool_size=1, max_overflow=1)
    warn_when_pool_saturates(engine, every_seconds=60)
    try:
        assert pool_status(engine) == {
            "size": 1,
            "max_overflow": 1,
            "checked_out": 0,
            "overflow": 0,
        }
        with caplog.at_level(logging.WARNING, logger="app.core.db"):
            async with engine.connect() as first:
                await first.execute(text("SELECT 1"))
                assert pool_status(engine)["checked_out"] == 1
                assert not [r for r in caplog.records if "saturated" in r.message]
                async with engine.connect() as second:
                    await second.execute(text("SELECT 1"))
                    status = pool_status(engine)
                    assert status["checked_out"] == 2 and status["overflow"] == 1
                    saturated = [r for r in caplog.records if "saturated" in r.message]
                    assert len(saturated) == 1
                    assert "2/2 connections" in saturated[0].message
            # A pool that fills again within the interval does not log again.
            async with engine.connect() as a, engine.connect() as b:
                await a.execute(text("SELECT 1"))
                await b.execute(text("SELECT 1"))
            assert len([r for r in caplog.records if "saturated" in r.message]) == 1
        assert pool_status(engine)["checked_out"] == 0
    finally:
        await engine.dispose()
