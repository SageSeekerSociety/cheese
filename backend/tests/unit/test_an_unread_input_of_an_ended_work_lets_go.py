"""An input nobody read stops holding its seat once its outcome can no longer be
reported.

The incident (2026-10-23 → 2026-10-30): a person's words were added into a
teammate's working turn in a task. The turn stopped three minutes later with the
addition unread, and the sandbox was released half an hour after that — before
the release ended such inputs (#4783). Nothing could report the addition read or
dropped any more, so it held the seat: from 2026-10-26 every message to that
teammate in the task was turned away (``unfinished_input``) and none ever
became a turn.

Right after its work ends, an unread addition may still be read, so it holds for
a while. Not for good: once the work has been over for as long as a turn may
produce nothing, it lets go, and so does an input whose work never got an
interval at all.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.core.config import settings
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
            "claude_code",
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
async def test_an_addition_to_a_work_over_for_days_no_longer_holds_its_seat(
    db_factory,
):
    """加进一轮里、没人读过的一条，那一轮已经停了七天：它不再按住座位，它带的那条
    消息也不再藏着，下一轮能带上它。"""
    topic = await a_topic(db_factory)
    work = await _work_that_ended(db_factory, topic, ago=timedelta(days=7))
    project, message = await _added_unread(
        db_factory, topic, work, registered_ago=timedelta(days=7, minutes=3)
    )

    assert not await _holds(db_factory, topic), (
        "那一轮停了七天，没人读过的那条还按着座位，之后点名这个队友的消息永远开始不了"
    )
    assert message not in await _hidden(db_factory, project, topic)


@pytest.mark.anyio
async def test_an_addition_to_a_work_that_just_ended_still_holds(db_factory):
    """那一轮一分钟前才停：会话可能还在读它，座位照旧按住。"""
    topic = await a_topic(db_factory)
    work = await _work_that_ended(db_factory, topic, ago=timedelta(minutes=1))
    project, message = await _added_unread(db_factory, topic, work)

    assert await _holds(db_factory, topic)
    assert message in await _hidden(db_factory, project, topic)


@pytest.mark.anyio
async def test_an_input_whose_work_never_got_an_interval_lets_go_in_time(db_factory):
    """一条输入写给的那一轮平台从没记下区间：刚登记时照旧按住（区间可能还在路上），
    过了时限就放开。"""
    young = await a_topic(db_factory, title="young")
    await _added_unread(db_factory, young, uuid.uuid4())
    assert await _holds(db_factory, young)

    old = await a_topic(db_factory, title="old")
    bound = timedelta(seconds=settings.turn_silence_timeout_s)
    await _added_unread(
        db_factory, old, uuid.uuid4(), registered_ago=bound + timedelta(minutes=5)
    )
    assert not await _holds(db_factory, old)
