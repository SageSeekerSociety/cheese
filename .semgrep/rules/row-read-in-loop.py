async def reads_inside_for(rows, repo):
    for row in rows:
        # ruleid: row-read-in-loop
        await repo.get_by_id(row.id)


async def reads_inside_async_for(rows, session):
    async for row in rows:
        # ruleid: row-read-in-loop
        await session.get(Topic, row.id)


async def reads_by_handle_inside_loop(rows, users):
    for row in rows:
        # ruleid: row-read-in-loop
        await users.user_by_handle(row.handle)


async def ignores_dictionary_get(rows):
    for row in rows:
        # ok: row-read-in-loop
        row.get("topic_id")


async def ignores_other_awaited_method(rows, cache):
    for row in rows:
        # ok: row-read-in-loop
        await cache.refresh(row.id)


async def reads_via_bare_helper_inside_loop(rows):
    for row in rows:
        # ruleid: row-read-in-loop
        await _people_seated(row.id)


async def ignores_other_bare_helper(rows):
    for row in rows:
        # ok: row-read-in-loop
        await _unrelated(row.id)
