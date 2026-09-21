"""The pool gauge says what is in use, and saturation is said out loud once."""

import logging

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.db import pool_status, warn_when_pool_saturates
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.anyio


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
