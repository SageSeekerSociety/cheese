"""A send to a seat whose runner is known to be alive asks the host nothing more.

A room's sessions as its sends start them, against a session host that counts
what it is asked. A session the core vouches for (started by this process, its
runner answering every read since) is handed back as it is while nothing it was
started with has changed; a change that needs a new session still gets one.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import PI, SessionRef
from app.domain.agent.harness.claude_code.protocol import INPUT_PROTOCOL
from app.domain.agent.harness.driven.runner import LONG_POLL
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.session_host import claude_code as claude_driver
from tests.integration import test_central_room_sessions as central_sessions
from tests.integration.test_central_room_sessions import (
    AGENT,
    CenterHub,
    ref,
    sessions,
)
from tests.integration.test_pi_runner_archive_crosses_once import pi_seat

#: A project with its agent and one room in it.
room = central_sessions.room


def _claude(client, monkeypatch) -> tuple[CenterHub, RoomSessions]:
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    hub = CenterHub()
    hub.ping = {
        "alive": True,
        "working": False,
        "tasks": {},
        "session_id": "s",
        "capabilities": [LONG_POLL],
        "input_protocol": INPUT_PROTOCOL,
    }
    central: Any = CentralChannel(
        DeviceChannel(hub=hub, session_factory=client.test_request_factory)
    )
    central._device_api_base = AsyncMock(return_value="http://central-api")
    return hub, sessions(central)


def _asked(hub: CenterHub) -> tuple[int, ...]:
    """Everything the session host has been asked, as counts."""
    return (
        len(hub.execs),
        len(hub.asked),
        len(hub.reasserted),
        len(hub.opened),
        len(hub.closed),
    )


@pytest.mark.anyio
async def test_a_live_claude_seat_that_has_not_changed_asks_its_host_nothing(
    client, room, monkeypatch
):
    project, topic = room
    hub, claude = _claude(client, monkeypatch)

    async def exercise():
        session = ref(project, topic)
        first = await claude.ensure(session, system_prompt="System", resume_token="s")
        before = _asked(hub)
        again = await claude.ensure(session, system_prompt="Another prompt")
        assert again == first
        assert _asked(hub) == before

    client.portal.call(exercise)


@pytest.mark.anyio
async def test_a_live_idle_claude_seat_whose_agent_changed_is_started_again(
    client, room, monkeypatch
):
    project, topic = room
    hub, claude = _claude(client, monkeypatch)

    async def exercise():
        session = ref(project, topic)
        await claude.ensure(
            session, system_prompt="System", env={"CHEESE_AGENT_CONFIG": "before"}
        )
        (screen,) = hub.opened
        await claude.ensure(
            session, system_prompt="System", env={"CHEESE_AGENT_CONFIG": "after"}
        )
        assert hub.closed == [screen.sid]
        assert len(hub.opened) == 1 and hub.opened[0].sid != screen.sid

    client.portal.call(exercise)


@pytest.mark.anyio
async def test_a_claude_seat_nobody_vouches_for_is_checked_on_the_host(
    client, room, monkeypatch
):
    project, topic = room
    hub, claude = _claude(client, monkeypatch)

    async def exercise():
        session = ref(project, topic)
        await claude.ensure(session, system_prompt="System")
        asked = len(hub.asked)
        hub.ping = {"alive": False}
        # The next process: it started nothing, so nothing vouches for the seat.
        restarted = sessions(claude.channel)
        with pytest.raises(Exception):  # noqa: B017 — nothing answers the greeting
            await restarted.ensure(session, system_prompt="System")
        assert ("ping", {}) in hub.asked[asked:]
        assert len(hub.closed) == 1

    monkeypatch.setattr(claude_driver, "STARTUP_WAIT_S", 0.0)
    client.portal.call(exercise)


@pytest.mark.anyio
async def test_a_live_pi_seat_is_launched_again_only_when_its_launch_changed(
    client, room, monkeypatch
):
    project, topic = room
    host, pi = pi_seat(client, monkeypatch)
    session = SessionRef(project, topic, AGENT, harness=PI)

    async def exercise():
        first = await pi.ensure(session, system_prompt="System", model="fixture")
        contract = host.contract
        host.programs.clear()
        again = await pi.ensure(
            session, system_prompt="Another prompt", model="fixture"
        )
        assert again == first
        assert host.programs == []

        await pi.ensure(session, system_prompt="System", model="another")
        assert len(host.programs) == 1
        assert host.pid == 2
        assert host.contract != contract

    client.portal.call(exercise)
