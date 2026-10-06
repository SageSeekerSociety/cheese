"""A runner the machine says is gone is asked once, not on every restart.

Every deploy restarts the backend, and each restart reads again the sessions
that outlived the one before. A session whose runner let it go long ago is
still placed, so each restart asked its machine again, and the same few
hundred gone runners made a burst of failed calls on every deploy.

Rules held here:

* a runner the online machine says is gone is asked at the first restart and
  not at the next ones, and its conversation still reads as over;
* a runner that could not be asked (the machine is away) is asked again;
* a session started again after that is asked again at the next restart.
"""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.domain.agent.device_hub import DeviceCallError
from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent_session.services import AgentSessionService
from tests.integration import test_central_room_sessions as central_sessions
from tests.integration.test_central_room_sessions import AGENT, channel, sessions

#: A project with its agent and one room in it.
room = central_sessions.room

GONE = DeviceCallError(
    "dial unix /tmp/cheese-execution-1000-0c5e4b916d2a.sock: "
    "connect: no such file or directory"
)


async def _place(factory, channel_name, topic) -> None:
    async with factory() as db:
        service = AgentSessionService(db)
        await service.remember_place(
            conversation_id=topic,
            agent_handle=AGENT,
            harness=CLAUDE_CODE,
            work_lease=None,
            runtime_location={
                "device_id": "center",
                "resource_id": str(topic),
                "channel": channel_name,
                "session_id": str(uuid.uuid4()),
                "runtime": {
                    "harness": CLAUDE_CODE,
                    "agent_handle": AGENT,
                    "state": "$HOME/.cheese/harness/fixture/claude-code/seat",
                },
            },
        )
        await service.remember(
            conversation_id=topic,
            agent_handle=AGENT,
            resume_token="the-old-conversation",
            harness=CLAUDE_CODE,
        )
        await db.commit()


def _seat(client, monkeypatch, answer):
    central = channel(client, monkeypatch)
    pings: list[str] = []

    async def runner(device, state, method, params, **_):
        if method == "ping":
            pings.append(state)
            raise answer
        return {}

    central._hub.call_executor.side_effect = runner
    # No screen is open on the session host: restoring them finds none.
    central._hub.list_screens = AsyncMock(return_value=[])
    return central, pings


@pytest.mark.anyio
async def test_a_gone_runner_is_asked_at_one_restart_and_not_the_next(
    client, room, monkeypatch
):
    _, topic = room
    central, pings = _seat(client, monkeypatch, GONE)
    factory = central._session_factory

    async def exercise():
        await _place(factory, central.name, topic)
        first = sessions(central)
        assert await first.recover("center") == []
        assert len(pings) == 1
        assert first.terminal_conversations

        second = sessions(central)
        assert await second.recover("center") == []
        assert len(pings) == 1
        # Not asked, still over: the stored answer stands for the runner.
        assert second.terminal_conversations == first.terminal_conversations

        # Started again: placed afresh, so the next restart asks again.
        await _place(factory, central.name, topic)
        assert await sessions(central).recover("center") == []
        assert len(pings) == 2

    client.portal.call(exercise)


@pytest.mark.anyio
async def test_a_runner_that_could_not_be_asked_is_asked_again(
    client, room, monkeypatch
):
    _, topic = room
    central, pings = _seat(client, monkeypatch, TimeoutError())
    factory = central._session_factory

    async def exercise():
        await _place(factory, central.name, topic)
        assert await sessions(central).recover("center") == []
        assert await sessions(central).recover("center") == []
        assert len(pings) == 2

    client.portal.call(exercise)
