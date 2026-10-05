"""Finding a room's feedback cards does not stop the backend for everyone else.

The room asks for its live proposal cards each time it opens. A room an agent
has worked in for weeks holds tens of thousands of blocks, nearly all of them
tool calls, and a handful of cards.
"""

import asyncio
import gc
import time

import pytest
from sqlalchemy import text

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.feedback.proposals import ProposalService
from app.domain.project.models import Project
from app.domain.topic.models import Topic, TopicKind
from tests.integration.conftest import a_team

pytestmark = pytest.mark.anyio

TOOL_CALLS = 40000


async def _worst_stall_during(work):
    """Run ``work`` and say the longest the loop went without a turn meanwhile.
    The collector is off while it runs: when a full collection lands depends
    on everything else the process holds, not on the read being measured."""
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


async def test_a_long_worked_room_finds_its_cards_without_holding_the_loop(
    db_factory,
):
    async with db_factory() as session:
        project = Project(team_id=await a_team(session), name="P", owner_handle="o")
        session.add(project)
        await session.flush()
        room = Topic(project_id=project.id, title="busy", kind=TopicKind.topic)
        session.add(room)
        await session.flush()
        await session.execute(
            text(
                "INSERT INTO blocks (project_id,topic_id,kind,author_type,author,"
                "content,refs,meta,id,created_at,updated_at) "
                "SELECT :pid,:tid,'event','participant','cheese',"
                "repeat('output of the command ', 20),'[]',"
                "json_build_object('tool','Bash','command','ls -la','exit',0),"
                "gen_random_uuid(),now()-(g||' seconds')::interval,now() "
                "FROM generate_series(1,:count) g"
            ),
            {"pid": project.id, "tid": room.id, "count": TOOL_CALLS},
        )

        def card(fingerprint, **extra):
            return Block(
                project_id=project.id,
                topic_id=room.id,
                kind=BlockKind.message,
                author_type=AuthorType.participant,
                author="cheese",
                content=fingerprint,
                meta={"feedback_proposal": {"fingerprint": fingerprint, **extra}},
            )

        waiting = card("waiting")
        sent = card("sent", accepted_feedback_id="00000000-0000-0000-0000-000000000001")
        session.add_all([waiting, sent])
        await session.flush()
        await session.execute(text("ANALYZE blocks"))
        service = ProposalService(session)
        await service.live_cards(room.id)

        cards, worst = await _worst_stall_during(service.live_cards(room.id))
        await session.rollback()

    assert [c["block_id"] for c in cards] == [str(waiting.id)]
    assert worst < 0.05, f"the loop was held {worst * 1000:.0f} ms"
