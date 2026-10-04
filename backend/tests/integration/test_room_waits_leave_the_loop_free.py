"""Reading which rooms wait on whom stays cheap however many turns have failed.

`GET /topics` asks it for every room of a project at once, and it looks back a
week. A busy project has hundreds of rooms and thousands of turns in that week
that ended in an error. Each of those turns used to travel as a bound value of
its own, so the SQL grew with them and was compiled on the event loop at every
read: on dev on 2026-10-04 that held the loop for about 100 ms per read, and
past 32767 values asyncpg refuses the query outright.
"""

import asyncio
import gc
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from app.domain.block.models import Block
from app.domain.block.waits import MemberWaits
from app.domain.project.models import Project
from app.domain.topic.models import Topic, TopicKind
from tests.integration.conftest import a_team

pytestmark = pytest.mark.anyio

ROOMS = 400


async def _project_with_failed_turns(session, count):
    project = Project(team_id=await a_team(session), name="P", owner_handle="o")
    session.add(project)
    await session.flush()
    rooms = [
        Topic(project_id=project.id, title=f"r{i}", kind=TopicKind.topic)
        for i in range(ROOMS)
    ]
    session.add_all(rooms)
    await session.flush()
    room_ids = [room.id for room in rooms]
    await session.execute(
        text(
            "INSERT INTO blocks (project_id,topic_id,kind,author_type,author,"
            "content,refs,meta,turn_id,id,created_at,updated_at) "
            "SELECT :pid, (CAST(:rooms AS uuid[]))[g % :n + 1], 'event',"
            "'platform','platform','x','[]',"
            "json_build_object('event_type','turn_failed','severity','error'),"
            "gen_random_uuid(),gen_random_uuid(),"
            "now()-(g||' seconds')::interval,now() "
            "FROM generate_series(1,:count) g"
        ),
        {"pid": project.id, "rooms": room_ids, "n": ROOMS, "count": count},
    )
    await session.execute(text("ANALYZE blocks"))
    return room_ids


async def _worst_stall_during(work):
    """Run ``work`` and say the longest the loop went without a turn meanwhile.

    The collector is off while it runs: a full collection can land inside any
    stretch that allocates, and how long it takes depends on everything else
    the process holds, not on the read being measured."""
    worst = 0.0
    finished = asyncio.Event()

    async def tick():
        nonlocal worst
        while not finished.is_set():
            before = time.perf_counter()
            await asyncio.sleep(0.005)
            worst = max(worst, time.perf_counter() - before - 0.005)

    gc.collect()
    gc.disable()
    ticking = asyncio.create_task(tick())
    await asyncio.sleep(0)
    try:
        result = await work
    finally:
        gc.enable()
        finished.set()
        await ticking
    return result, worst


def _all_failed(found, room_ids):
    assert set(found) == set(room_ids)
    assert all(w.reason == "failed" for ws in found.values() for w in ws)


async def test_a_project_with_many_failed_turns_is_read_without_holding_the_loop(
    db_factory,
):
    """Measured against reading the failed turns themselves, which any answer
    has to do, so the bound holds on a slow machine as on a fast one. Going
    through each of them once more in Python is the rest of the allowance."""
    async with db_factory() as session:
        room_ids = await _project_with_failed_turns(session, 12000)
        waits = MemberWaits(session)
        # The first read prepares what later ones reuse; a page polls this.
        await waits.for_rooms(room_ids, now=datetime.now(UTC))

        async def the_rows():
            rows = select(Block.topic_id, Block.turn_id, Block.created_at).where(
                Block.topic_id.in_(room_ids)
            )
            return (await session.execute(rows)).all()

        await the_rows()
        _, reading = await _worst_stall_during(the_rows())
        found, worst = await _worst_stall_during(
            waits.for_rooms(room_ids, now=datetime.now(UTC))
        )
        await session.rollback()

    _all_failed(found, room_ids)
    assert worst < 4 * reading, (
        f"the loop was held {worst * 1000:.0f} ms; "
        f"reading the failed turns held it {reading * 1000:.0f} ms"
    )


async def test_more_failed_turns_than_a_query_can_bind_are_still_read(db_factory):
    async with db_factory() as session:
        room_ids = await _project_with_failed_turns(session, 17000)
        found = await MemberWaits(session).for_rooms(room_ids, now=datetime.now(UTC))
        await session.rollback()

    _all_failed(found, room_ids)
