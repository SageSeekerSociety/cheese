"""The liveness probe the orphan sweep judges silence on.

`SchedulerService.last_block_at` is the only DB-backed half of the wedged-turn
verdict, and the whole feature rests on it: if it reports a topic as quieter
than it is, the sweep cancels live work. It is also the signal a human checks by
hand (「最后一块是几点」), so it has to agree with what the topic shows.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.domain.agent.chat import ChatService
from app.domain.block.models import AuthorType, Block
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.scheduler.service import SchedulerService
from app.domain.topic.services import TopicService
from tests.conftest import stub_compute


@pytest.mark.anyio
async def test_last_block_at_reports_the_newest_block_per_topic(client, tmp_path):
    factory = client.test_factory
    svc = SchedulerService(
        chat_service=ChatService(
            session_factory=factory,
            base_system_prompt="你是芝士。",
            workspace_root=str(tmp_path / "ws"),
            compute=stub_compute(),
        )
    )

    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topics = TopicService(session)
        chatty = await topics.create(project_id=project.id, title="A", created_by="u")
        quiet = await topics.create(project_id=project.id, title="Q", created_by="u")
        empty = await topics.create(project_id=project.id, title="E", created_by="u")
        blocks = BlockRepository(session)
        for topic in (chatty, quiet):
            for _ in range(2):
                await blocks.add(
                    project_id=project.id,
                    topic_id=topic.id,
                    author="u",
                    author_type=AuthorType.human,
                    content="hi",
                )
        # Backdate every block of the quiet topic — this is the shape a wedged
        # turn leaves behind: registered and running, last block hours old.
        await session.execute(
            update(Block)
            .where(Block.topic_id == quiet.id)
            .values(created_at=datetime.now(UTC) - timedelta(hours=8))
        )
        await session.commit()

    got = await svc.last_block_at({chatty.id, quiet.id, empty.id})

    now = datetime.now(UTC)
    # Every returned timestamp is tz-aware: the sweep subtracts it from a real
    # clock, so a naive value would raise rather than merely read wrong.
    assert all(ts.tzinfo is not None for ts in got.values())
    assert (now - got[chatty.id]).total_seconds() < 300
    assert (now - got[quiet.id]).total_seconds() > 7 * 3600
    # A topic with no blocks is absent rather than present-and-zero, so the
    # caller falls back to the turn's own start time instead of reading an
    # epoch timestamp as "silent since 1970" and cancelling on the spot.
    assert empty.id not in got


@pytest.mark.anyio
async def test_last_block_at_is_empty_for_no_topics(client, tmp_path):
    """The sweep calls this with whatever is in `_live`, which is usually
    nothing — that must not turn into a `WHERE topic_id IN ()` round trip."""
    svc = SchedulerService(
        chat_service=ChatService(
            session_factory=client.test_factory,
            base_system_prompt="你是芝士。",
            workspace_root=str(tmp_path / "ws"),
            compute=stub_compute(),
        )
    )
    assert await svc.last_block_at(set()) == {}
    assert await svc.last_block_at({uuid.uuid4()}) == {}
