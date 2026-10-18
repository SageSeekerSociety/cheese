async def reads_inside_for(rows, session):
    for row in rows:
        # ruleid: row-read-in-loop
        await session.get(Topic, row.id)


async def reads_inside_async_for(rows, db):
    async for row in rows:
        # ruleid: row-read-in-loop
        await db.get(Topic, row.id)


async def reads_through_session_field(rows, self):
    for row in rows:
        # ruleid: row-read-in-loop
        await self._session.get(Topic, row.id)


async def reads_through_db_field(rows, self):
    async for row in rows:
        # ruleid: row-read-in-loop
        await self._db.get(Topic, row.id)


async def ignores_dictionary_get(rows):
    for row in rows:
        # ok: row-read-in-loop
        row.get("topic_id")


async def ignores_other_awaited_get(rows, cache):
    for row in rows:
        # ok: row-read-in-loop
        await cache.get(row.id)
