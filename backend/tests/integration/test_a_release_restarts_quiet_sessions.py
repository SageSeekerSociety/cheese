"""A release restarts a quiet session before anyone writes to it.

A release that changes how sessions are launched leaves the ones already running
as they were; a session is compared with what the backend would start today
only when it is started. That comparison used to wait for the next message, so
the person who sent it waited for the new session to come up. Here the process
that takes the room over makes it as soon as it has read the session up, and a
session that was busy then is restarted once it goes quiet — never while it is
working, and never by a process that does not own the running work.
"""

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness.claude_code.protocol import INPUT_PROTOCOL
from app.domain.agent.harness.driven.runner import LONG_POLL
from tests.integration import test_central_room_sessions as central_sessions
from tests.integration.test_central_room_sessions import CenterHub, ref, sessions
from tests.support.hang import HANG_S

#: A project with its agent and one room in it.
room = central_sessions.room

IDLE = {
    "alive": True,
    "working": False,
    "tasks": {},
    "session_id": "s",
    "capabilities": [LONG_POLL],
    "input_protocol": INPUT_PROTOCOL,
}
BUSY = {**IDLE, "tasks": {"t1": "local_bash"}}


class ReleaseHub(CenterHub):
    """The session host, whose runners have written nothing nobody read."""

    async def call_executor(self, device_id, state, method, params, **options):
        if device_id == "center" and method == "events":
            if params.get("wait"):
                await asyncio.sleep(0.05)
            return {"events": []}
        return await super().call_executor(device_id, state, method, params, **options)


def _backend(client, hub, tmp_path, *, owns: bool = True):
    """One backend process: its room sessions over ``hub``, and the service."""
    executor = DeviceChannel(hub=hub, session_factory=client.test_request_factory)
    central: Any = CentralChannel(executor)
    central._device_api_base = AsyncMock(return_value="http://central-api")
    claude = sessions(central)
    claude.bind_owns_sessions(lambda: SimpleNamespace(owns_sessions=lambda: owns))
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="System",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([claude], claude.name),
    )
    return service


async def _until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(HANG_S):
        while not condition():
            await asyncio.sleep(0.02)


async def _started_by_the_previous_release(client, hub, tmp_path, session):
    previous = sessions(
        CentralChannel(
            DeviceChannel(hub=hub, session_factory=client.test_request_factory)
        )
    )
    previous.channel._device_api_base = AsyncMock(return_value="http://central-api")
    await previous.ensure(
        session,
        system_prompt="System",
        env={"CHEESE_AGENT_CONFIG": "what the previous release started"},
    )
    (screen,) = hub.opened
    return screen


@pytest.mark.anyio
async def test_a_quiet_session_is_restarted_when_the_release_takes_it_over(
    client, room, monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    project, topic = room
    hub = ReleaseHub()
    hub.ping = dict(IDLE)

    async def exercise():
        session = ref(project, topic)
        old = await _started_by_the_previous_release(client, hub, tmp_path, session)
        service = _backend(client, hub, tmp_path)

        # Nobody writes to the room.
        await service.recover_sessions()
        await _until(lambda: old.sid in hub.closed and len(hub.opened) == 1)

        (new,) = hub.opened
        assert new.sid != old.sid
        # Brought up to date once: going quiet again restarts nothing.
        service.prewarm.nudge(session)
        await asyncio.sleep(0.5)
        assert [s.sid for s in hub.opened] == [new.sid]
        assert hub.closed == [old.sid]
        await service.stop_listening(0)

    client.portal.call(exercise)


@pytest.mark.anyio
async def test_a_busy_session_is_left_running_and_restarted_once_it_goes_quiet(
    client, room, monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    project, topic = room
    hub = ReleaseHub()
    hub.ping = dict(IDLE)

    async def exercise():
        session = ref(project, topic)
        old = await _started_by_the_previous_release(client, hub, tmp_path, session)
        service = _backend(client, hub, tmp_path)

        # Its background command is still running when the release takes over.
        hub.ping = dict(BUSY)
        await service.recover_sessions()
        await asyncio.sleep(0.5)
        assert hub.closed == []
        assert [s.sid for s in hub.opened] == [old.sid]

        # The command finished: the session went quiet.
        hub.ping = dict(IDLE)
        service.prewarm.nudge(session)
        await _until(lambda: old.sid in hub.closed and len(hub.opened) == 1)
        assert hub.opened[0].sid != old.sid
        await service.stop_listening(0)

    client.portal.call(exercise)


@pytest.mark.anyio
async def test_a_backend_that_does_not_own_the_work_restarts_nothing(
    client, room, monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    project, topic = room
    hub = ReleaseHub()
    hub.ping = dict(IDLE)

    async def exercise():
        session = ref(project, topic)
        old = await _started_by_the_previous_release(client, hub, tmp_path, session)
        service = _backend(client, hub, tmp_path, owns=False)

        await service.recover_sessions()
        service.prewarm.nudge(session)
        await asyncio.sleep(0.5)
        assert hub.closed == []
        assert [s.sid for s in hub.opened] == [old.sid]
        await service.stop_listening(0)

    client.portal.call(exercise)
