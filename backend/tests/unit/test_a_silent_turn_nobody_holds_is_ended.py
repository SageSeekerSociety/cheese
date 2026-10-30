"""A turn no session holds, that has said nothing past the silence horizon, is
ended and its room told.

The incident (2026-10-11 → 2026-10-30): a thread's turn was delivered, the
agent replied twice and asked a question, and its sandbox was then released.
Nothing was left to report the turn's end, and nothing had pronounced its
conversation dead, so the orphan sweep kept the row open as "unknown"
(`death_evidence.unknown_row`, FB-56 legacy③) on every tick for nineteen days.
The person's answer and three follow-ups queued behind that open row
(`pending_messages`: ``turn_open_in_db``) and never became a turn.

Unknown is not dead, and a takeover must not judge it — a device's screens may
dial in minutes after a deploy. But a row nobody holds that has put nothing on
record for as long as a healthy turn can be quiet is wedged, like a silent turn
this process runs.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.block.models import Block, BlockKind
from app.domain.conversation.models import Conversation
from app.domain.event_author import AuthorType
from tests.turn_log import a_topic, open_turn, turn_row
from tests.unit.test_runtime import _SweepChat

SILENCE_S = 1800.0
NINETEEN_DAYS_S = 19 * 24 * 3600


async def _agent_said(factory, topic_id, turn_id, at: datetime) -> None:
    """A reply the turn posted, at ``at``."""
    async with factory() as session:
        conversation = await session.get(Conversation, topic_id)
        assert conversation is not None
        session.add(
            Block(
                id=uuid.uuid4(),
                project_id=conversation.project_id,
                conversation_id=topic_id,
                kind=BlockKind.message,
                author_type=AuthorType.participant,
                author="cheese-test",
                content="我去代码里核实，几分钟后回你。",
                turn_id=turn_id,
                created_at=at,
                updated_at=at,
            )
        )
        await session.commit()


def _room_just_spoke(topic_id):
    """The room's newest block is a person's 「你卡了？」 a minute ago."""

    async def last_activity(topic_ids):
        assert topic_ids <= {topic_id}
        return {topic_id: datetime.now(UTC) - timedelta(minutes=1)}

    return last_activity


@pytest.mark.anyio
async def test_a_turn_nobody_holds_that_said_nothing_past_the_horizon_is_ended(
    db_factory,
):
    """其会话谁也接不上、也没人说它死了的一轮，十九天一个字没写：接手时不动它，
    钟点扫描把它判卡死、关掉，并在房间里说一声。房间里有人追问不算这一轮在动。"""
    topic = await a_topic(db_factory)
    turn = await open_turn(
        db_factory,
        topic,
        delivered=True,
        age_s=NINETEEN_DAYS_S,
        resendable=False,
        session_id="sess-gone",
    )
    await _agent_said(
        db_factory, topic, turn, datetime.now(UTC) - timedelta(seconds=NINETEEN_DAYS_S)
    )
    runner = AgentWorkRunner(InProcessBroker())
    chat = _SweepChat(db_factory, live_screen=False, seat_state="unknown")

    # Takeover: screens may still dial in, so nothing is judged.
    await runner.resume_orphans(chat)
    row = await turn_row(db_factory, turn)
    assert row is not None and row.stopped_at is None, "接手那一刻就把它判死了"

    await runner.sweep_orphans(
        chat, last_activity=_room_just_spoke(topic), silence_s=SILENCE_S
    )

    row = await turn_row(db_factory, turn)
    assert row is not None and row.stopped_at is not None, (
        "一轮十九天没有任何记录、没有会话接得上，扫描还让它开着，排在它后面的消息永远起不了轮"
    )
    assert len(chat.texts) == 1, f"房间里该有且只有一句说明，实际 {chat.texts}"


@pytest.mark.anyio
async def test_a_turn_nobody_holds_that_spoke_recently_stays_open(db_factory):
    """同样谁也接不上，但这一轮五分钟前还写过一条：它可能还在干活，不动它。"""
    topic = await a_topic(db_factory)
    turn = await open_turn(
        db_factory,
        topic,
        delivered=True,
        age_s=6 * 3600,
        resendable=False,
        session_id="sess-out-of-reach",
    )
    await _agent_said(db_factory, topic, turn, datetime.now(UTC) - timedelta(minutes=5))
    runner = AgentWorkRunner(InProcessBroker())
    chat = _SweepChat(db_factory, live_screen=False, seat_state="unknown")

    await runner.sweep_orphans(
        chat, last_activity=_room_just_spoke(topic), silence_s=SILENCE_S
    )

    row = await turn_row(db_factory, turn)
    assert row is not None and row.stopped_at is None, "一轮五分钟前还在写，被判死了"
    assert chat.texts == []
