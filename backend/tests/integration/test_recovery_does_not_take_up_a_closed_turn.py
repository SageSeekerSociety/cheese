"""Recovery does not take up a turn the sweep already closed.

The sweep closes a silent turn nobody holds, and recovery takes a turn up again
when its session comes back. They decide the same turn's fate, so they are
serialized on its row: whichever holds it first decides, and the other sees the
decision. A session that comes back after the sweep closed its turn finds the
turn over; it does not become a running turn again beside the one the queued
messages are about to start.
"""

from datetime import UTC, datetime

import pytest

from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.agent.runtime import AgentWorkRunner
from tests.integration.test_session_turn_event_receiver import _chat, _room
from tests.turn_log import open_turn


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
