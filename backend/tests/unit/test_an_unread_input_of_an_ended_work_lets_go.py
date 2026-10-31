"""An input nobody read keeps holding its seat while something may still read
it, however long ago its work ended. When it lets go is
`tests/integration/test_an_input_is_released_only_by_its_own_process.py`.

The incident (2026-10-23 → 2026-10-30): a person's words were added into a
teammate's working turn in a task. The turn stopped three minutes later with the
addition unread, the session's process exited half an hour later, and the
sandbox was released — before the release ended such inputs (#4783). Nothing
would report the addition read or dropped any more, so it held the seat: from
2026-10-26 every message to that teammate in the task was turned away
(``unfinished_input``) and none ever became a turn.

Time alone is not that knowledge. A runner keeps an addition it has not read
past the end of its work and can still read it later; releasing the seat on a
clock would hand the same message to a second input, and both could be read.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.domain.agent.repositories import AgentTurnRepository
from app.domain.block.models import Block, BlockKind
from app.domain.conversation.models import Conversation
from app.domain.delivery.input_holds import seat_has_unfinished_input
from app.domain.delivery.input_identity import InputEffects, InputIdentity
from app.domain.delivery.models import NativeInput
from app.domain.delivery.receipts import held_blocks, register_input
from app.domain.event_author import AuthorType
from tests.turn_log import a_topic, open_turn

SEAT = "cheese-test"
SESSION = "sess-released"
HARNESS = "claude_code"


async def _work_that_ended(factory, topic_id, *, ago: timedelta) -> uuid.UUID:
    """A turn the platform ended ``ago``."""
    turn = await open_turn(
        factory, topic_id, delivered=True, age_s=ago.total_seconds() + 180
    )
    async with factory() as session:
        await AgentTurnRepository(session).close([turn], datetime.now(UTC) - ago)
        await session.commit()
    return turn


async def _added_unread(factory, topic_id, work_id, *, registered_ago=timedelta()):
    """A person's message added into ``work_id`` and never read: the input that
    carried it, and the message."""
    async with factory() as session:
        conversation = await session.get(Conversation, topic_id)
        assert conversation is not None
        message = Block(
            id=uuid.uuid4(),
            project_id=conversation.project_id,
            conversation_id=topic_id,
            kind=BlockKind.message,
            author_type=AuthorType.participant,
            author="u",
            content="这里也看一下",
            meta={"consumed_turn": None},
        )
        session.add(message)
        await session.commit()
        identity = InputIdentity(
            conversation.project_id,
            topic_id,
            SEAT,
            HARNESS,
            SESSION,
            uuid.uuid4(),
            work_id,
        )
        await register_input(
            session, identity, InputEffects(held_block_ids=(message.id,))
        )
        await session.execute(
            update(NativeInput)
            .where(NativeInput.input_id == identity.input_id)
            .values(registered_at=datetime.now(UTC) - registered_ago)
        )
        await session.commit()
        return conversation.project_id, message.id


async def _holds(factory, topic_id) -> bool:
    async with factory() as session:
        return await seat_has_unfinished_input(session, topic_id, SEAT)


async def _hidden(factory, project_id, topic_id) -> set[uuid.UUID]:
    async with factory() as session:
        return await held_blocks(
            session, project_id=project_id, topic_id=topic_id, recipient_handle=SEAT
        )


@pytest.mark.anyio
async def test_an_addition_its_process_may_still_read_holds_however_old(db_factory):
    """那一轮停了七天，但接收它的会话进程从没报过退出：进程可能还留着它、之后还会读。
    这时放开座位，同一条消息会进第二条输入，两条都可能被读到，所以照旧按住。"""
    topic = await a_topic(db_factory)
    work = await _work_that_ended(db_factory, topic, ago=timedelta(days=7))
    project, message = await _added_unread(
        db_factory, topic, work, registered_ago=timedelta(days=7, minutes=3)
    )

    assert await _holds(db_factory, topic)
    assert message in await _hidden(db_factory, project, topic)
