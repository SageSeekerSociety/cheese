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
from sqlalchemy import select

from app.domain.agent import runtime_orphans
from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.block.models import Block, BlockKind
from app.domain.conversation.models import Conversation
from app.domain.event_author import AuthorType
from app.domain.notice_meta import EVENT_TURN_TIMEOUT
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


async def _lines_about(factory, turn_id) -> list[str]:
    """What the room was told about this turn having stopped."""
    async with factory() as session:
        blocks = await session.scalars(select(Block).where(Block.turn_id == turn_id))
        return [
            block.content
            for block in blocks
            if (block.meta or {}).get("event_type") == EVENT_TURN_TIMEOUT
        ]


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
    lines = await _lines_about(db_factory, turn)
    assert len(lines) == 1, f"房间里该有且只有一句说明，实际 {lines}"


@pytest.mark.anyio
async def test_a_session_that_comes_back_while_the_sweep_reads_keeps_its_turn(
    db_factory, monkeypatch
):
    """扫描在读这一轮的记录时，它的会话连回来了：这一轮已经有人接着，不能关。"""
    topic = await a_topic(db_factory)
    turn = await open_turn(
        db_factory,
        topic,
        delivered=True,
        age_s=NINETEEN_DAYS_S,
        resendable=False,
        session_id="sess-back",
    )
    runner = AgentWorkRunner(InProcessBroker())
    chat = _SweepChat(db_factory, live_screen=False, seat_state="unknown")
    read_progress = runtime_orphans._last_progress

    async def read_while_the_session_comes_back(*args, **kwargs):
        progress = await read_progress(*args, **kwargs)
        chat._live_screen = True
        chat._seat_state = "live"
        return progress

    monkeypatch.setattr(
        runtime_orphans, "_last_progress", read_while_the_session_comes_back
    )

    await runner.sweep_orphans(
        chat, last_activity=_room_just_spoke(topic), silence_s=SILENCE_S
    )

    row = await turn_row(db_factory, turn)
    assert row is not None and row.stopped_at is None, (
        "会话在扫描读记录时连了回来，它正在跑的这一轮还是被关了"
    )
    assert await _lines_about(db_factory, turn) == []


@pytest.mark.anyio
async def test_a_silent_turn_whose_line_cannot_be_written_stays_open(
    db_factory, monkeypatch
):
    """说明那一句写不进去时，这一轮不关：关了却没告诉房间，等着的人就永远不知道。
    下一次扫描再来，关掉和说明一起落下。"""
    topic = await a_topic(db_factory)
    turn = await open_turn(
        db_factory,
        topic,
        delivered=True,
        age_s=NINETEEN_DAYS_S,
        resendable=False,
        session_id="sess-gone",
    )
    runner = AgentWorkRunner(InProcessBroker())
    chat = _SweepChat(db_factory, live_screen=False, seat_state="unknown")
    from app.domain.agent import room_events

    write_line = room_events.announce

    async def fails(*args, **kwargs):
        raise RuntimeError("the line could not be written")

    monkeypatch.setattr(room_events, "announce", fails)
    await runner.sweep_orphans(
        chat, last_activity=_room_just_spoke(topic), silence_s=SILENCE_S
    )
    row = await turn_row(db_factory, turn)
    assert row is not None and row.stopped_at is None, "说明没写进去，这一轮却关了"

    monkeypatch.setattr(room_events, "announce", write_line)
    await runner.sweep_orphans(
        chat, last_activity=_room_just_spoke(topic), silence_s=SILENCE_S
    )
    row = await turn_row(db_factory, turn)
    assert row is not None and row.stopped_at is not None
    assert len(await _lines_about(db_factory, turn)) == 1


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
    assert await _lines_about(db_factory, turn) == []
