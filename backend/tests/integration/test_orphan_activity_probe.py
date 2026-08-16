"""The liveness probe the orphan sweep judges silence on.

`SchedulerService.last_turn_block_at` is the only DB-backed half of the
wedged-turn verdict, and the whole feature rests on it: if it reports a turn as
quieter than it is, the sweep cancels live work — and if it reports a dead turn
as alive, the topic stays wedged forever. It is also the signal a human checks
by hand (「这一轮最后说话是几点」), so it has to agree with what the topic shows.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.domain.agent.chat import ChatService
from app.domain.agent.service import AgentService
from app.domain.block.models import AuthorType, Block
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.scheduler.service import SchedulerService
from app.domain.topic.services import TopicService


def _scheduler(client, tmp_path) -> SchedulerService:
    return SchedulerService(
        chat_service=ChatService(
            session_factory=client.test_factory,
            agent=AgentService(model="stub"),
            base_system_prompt="你是芝士。",
            workspace_root=str(tmp_path / "ws"),
        )
    )


@pytest.mark.anyio
async def test_last_turn_block_at_reports_the_newest_block_per_turn(client, tmp_path):
    factory = client.test_factory
    svc = _scheduler(client, tmp_path)
    chatty_turn, quiet_turn, empty_turn = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="A", created_by="u"
        )
        blocks = BlockRepository(session)
        for turn in (chatty_turn, quiet_turn):
            for _ in range(2):
                await blocks.add(
                    project_id=project.id,
                    topic_id=topic.id,
                    author="u",
                    author_type=AuthorType.human,
                    content="hi",
                    turn_id=turn,
                )
        # Backdate every block of the quiet turn — this is the shape a wedged
        # turn leaves behind: registered and running, last block hours old.
        await session.execute(
            update(Block)
            .where(Block.turn_id == quiet_turn)
            .values(created_at=datetime.now(UTC) - timedelta(hours=8))
        )
        await session.commit()

    got = await svc.last_turn_block_at({chatty_turn, quiet_turn, empty_turn})

    now = datetime.now(UTC)
    # Every returned timestamp is tz-aware: the sweep subtracts it from a real
    # clock, so a naive value would raise rather than merely read wrong.
    assert all(ts.tzinfo is not None for ts in got.values())
    assert (now - got[chatty_turn]).total_seconds() < 300
    assert (now - got[quiet_turn]).total_seconds() > 7 * 3600
    # A turn with no blocks is absent rather than present-and-zero, so the
    # caller falls back to the turn's own start time instead of reading an
    # epoch timestamp as "silent since 1970" and cancelling on the spot.
    assert empty_turn not in got


@pytest.mark.anyio
async def test_a_later_message_does_not_revive_a_wedged_turns_clock(client, tmp_path):
    """The regression that made a stuck room stay stuck.

    The probe used to be scoped to the TOPIC, so a new message — which is a
    block, in the same topic, right now — reset the wedged turn's silence clock.
    Every attempt to wake the room therefore guaranteed the sweep would never
    cancel the turn holding its lock. Scoped to the turn, the corpse stays cold
    no matter how much anyone else says."""
    factory = client.test_factory
    svc = _scheduler(client, tmp_path)
    wedged_turn, later_turn = uuid.uuid4(), uuid.uuid4()

    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="A", created_by="u"
        )
        blocks = BlockRepository(session)
        await blocks.add(
            project_id=project.id,
            topic_id=topic.id,
            author="cheese",
            author_type=AuthorType.ai,
            content="开始干活",
            turn_id=wedged_turn,
        )
        await session.execute(
            update(Block)
            .where(Block.turn_id == wedged_turn)
            .values(created_at=datetime.now(UTC) - timedelta(hours=3))
        )
        # …and now someone, staring at a room that has said nothing for hours,
        # sends another message. It belongs to ITS OWN turn.
        await blocks.add(
            project_id=project.id,
            topic_id=topic.id,
            author="u",
            author_type=AuthorType.human,
            content="在吗？",
            turn_id=later_turn,
        )
        await session.commit()

    got = await svc.last_turn_block_at({wedged_turn})

    assert (datetime.now(UTC) - got[wedged_turn]).total_seconds() > 2 * 3600


@pytest.mark.anyio
async def test_last_turn_block_at_is_empty_for_no_turns(client, tmp_path):
    """The sweep calls this with whatever is in `_live`, which is usually
    nothing — that must not turn into a `WHERE turn_id IN ()` round trip."""
    svc = _scheduler(client, tmp_path)
    assert await svc.last_turn_block_at(set()) == {}
    assert await svc.last_turn_block_at({uuid.uuid4()}) == {}
