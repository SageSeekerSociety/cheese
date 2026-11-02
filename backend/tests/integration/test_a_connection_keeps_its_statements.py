"""A pooled connection keeps the statements it prepared across a working set
wider than the driver's default of 100.

A statement that falls out of the per-connection cache is prepared again on
its next run: one more round trip and a fresh plan, about 2.5 ms against
0.3 ms for a kept one. The hot paths issue about 600 distinct statements, and
at the default a third of their executions were prepared again — the merged
conversation read showed 5–6 ms in production's Server-Timing for that."""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import db as app_db
from app.core.config import settings


def test_a_statement_stays_prepared_while_two_hundred_others_run(client):
    async def go() -> tuple[bool, int]:
        # Built as the application's engine is, on this test's own loop.
        engine = create_async_engine(app_db.engine.url, **app_db._engine_kwargs)
        try:
            async with engine.connect() as conn:
                first = "SELECT 1 AS kept_statement_0"
                await conn.execute(text(first))
                for k in range(1, 200):
                    await conn.execute(text(f"SELECT {k} AS kept_statement_{k}"))
                raw = await conn.get_raw_connection()
                cache = raw.dbapi_connection._prepared_statement_cache
                return first in cache, cache.capacity
        finally:
            await engine.dispose()

    kept, capacity = asyncio.run(go())
    assert capacity == settings.db_statement_cache_size
    assert kept, "the first statement was evicted by 199 others"
