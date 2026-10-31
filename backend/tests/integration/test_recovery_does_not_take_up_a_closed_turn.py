"""Recovery does not take up a turn the sweep already closed.

The sweep closes a silent turn nobody holds, and recovery takes a turn up again
when its session comes back. They decide the same turn's fate, so they are
serialized on its row: whichever holds it first decides, and the other sees the
decision. A session that comes back after the sweep closed its turn finds the
turn over; it does not become a running turn again beside the one the queued
messages are about to start — not when recovery takes it up, and not when the
session's own output for it is replayed. What the session still says lands in
the room with no turn running.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.domain.agent.models import AgentTurn
from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.agent.service import AgentToolUse
from tests.integration.test_session_turn_event_receiver import _chat, _room
from tests.turn_log import open_turn, turn_row
from tests.unit.test_a_silent_turn_nobody_holds_is_ended import _lines_about


@pytest.mark.anyio
async def test_a_turn_the_sweep_closed_is_not_taken_up_when_its_session_returns(
    business_db_factory, tmp_path
):
    factory = business_db_factory
    project, topic = await _room(factory, "Closed before the session returned")
    turn = await open_turn(
        factory, topic, delivered=True, age_s=19 * 86400, session_id="sess-back"
    )
    async with factory() as session:
        await AgentTurnRepository(session).close([turn], datetime.now(UTC))
        await session.commit()
    chat = _chat(factory, tmp_path, AgentWorkRunner(InProcessBroker()))

    taken = await chat.native_turns.begin(project, topic, turn, opened=True)

    assert taken is None, "扫描已经关掉的一轮，会话回来时又被当成在跑的一轮接上了"
    assert (topic, turn) not in chat.live.hook_work


@pytest.mark.anyio
async def test_an_open_turn_is_taken_up_when_its_session_returns(
    business_db_factory, tmp_path
):
    factory = business_db_factory
    project, topic = await _room(factory, "Open when the session returned")
    turn = await open_turn(
        factory, topic, delivered=True, age_s=600, session_id="sess-back"
    )
    chat = _chat(factory, tmp_path, AgentWorkRunner(InProcessBroker()))

    taken = await chat.native_turns.begin(project, topic, turn, opened=True)

    assert taken is not None
    assert (topic, turn) in chat.live.hook_work


@pytest.mark.anyio
async def test_output_replayed_for_a_closed_turn_does_not_run_it_again(
    business_db_factory, tmp_path
):
    """扫描关掉了一轮会话自己开的轮并在房间里说了，会话回来后补发它当时的输出：
    这一轮不重新算在跑。"""
    factory = business_db_factory
    project, topic = await _room(factory, "Output replayed after the close")
    turn = uuid.uuid4()
    first = _chat(factory, tmp_path, AgentWorkRunner(InProcessBroker()))
    started = await first.native_turns.begin(project, topic, turn, session_id="sess")
    assert started is not None
    await started.known_commits
    long_ago = datetime.now(UTC) - timedelta(days=19)
    async with factory() as session:
        await session.execute(
            update(AgentTurn)
            .where(AgentTurn.id == turn)
            .values(started_at=long_ago, delivered_at=long_ago)
        )
        await session.commit()
    runner = AgentWorkRunner(InProcessBroker())
    chat = _chat(factory, tmp_path, runner)

    async def room_just_spoke(topic_ids):
        return {topic_id: datetime.now(UTC) for topic_id in topic_ids}

    await runner.sweep_orphans(chat, last_activity=room_just_spoke, silence_s=1800)
    row = await turn_row(factory, turn)
    assert row is not None and row.stopped_at is not None
    assert len(await _lines_about(factory, turn)) == 1

    await chat.hook_events.accept(
        project,
        topic,
        turn,
        AgentToolUse(name="Bash", input={"command": "true"}),
        "late-unsolicited-output",
        False,
        True,
    )

    revived = chat.live.hook_work.get((topic, turn))
    if revived is not None and revived.known_commits is not None:
        await revived.known_commits
    assert revived is None, "补发的输出让一轮已经关掉的轮重新算在跑"
    assert turn not in chat.live.active_turn_ids.get(topic, set())
    row = await turn_row(factory, turn)
    assert row is not None and row.stopped_at is not None
