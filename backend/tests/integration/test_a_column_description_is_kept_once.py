"""Statements a connection keeps prepared share one copy of each column's
description (`core/statement_attributes`).

The copies are tuple subclasses the garbage collector tracks for as long as
they live, and the cache keeps hundreds of statements per connection: they
were a fifth of what production's full collections walked."""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import db as app_db


def test_two_statements_with_the_same_column_share_its_description(client):
    async def go():
        engine = create_async_engine(app_db.engine.url, **app_db._engine_kwargs)
        try:
            async with engine.connect() as conn:
                first = "SELECT 1 AS id, 'a'::text AS name"
                second = "SELECT 2 AS id, 'b'::text AS label"
                one = await conn.execute(text(first))
                two = await conn.execute(text(second))
                raw = await conn.get_raw_connection()
                cache = raw.dbapi_connection._prepared_statement_cache
                return (
                    cache[first][1],
                    cache[second][1],
                    list(one.keys()),
                    list(two.keys()),
                )
        finally:
            await engine.dispose()

    first, second, one_keys, two_keys = asyncio.run(go())
    assert one_keys == ["id", "name"] and two_keys == ["id", "label"]
    assert first[0] is second[0], "the same column is described twice"
    assert first[1].type is second[1].type, "the same type is described twice"
