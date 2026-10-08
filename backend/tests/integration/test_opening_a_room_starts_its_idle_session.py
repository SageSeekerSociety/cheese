"""Opening a room, or typing in it, starts the session its runner let go.

A session that sat idle for ten minutes is let go by its runner, and the next
message used to wait for it to start again. Somebody opening the room or typing
in it is the sign that a message is coming, so the session is started then —
once a minute at most, and only when the session host has room for it.
"""

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.api import deps as session_turn_deps
from app.api.deps import get_chat_service
from app.core.config import settings
from app.domain.agent import prewarm
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.device_hub import DeviceCallError
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.main import app
from tests.integration import test_central_room_sessions as central_sessions
from tests.integration.conftest import room_socket
from tests.integration.test_a_release_restarts_quiet_sessions import IDLE, ReleaseHub
from tests.integration.test_central_room_sessions import sessions
from tests.support.hang import HANG_S

#: A project with its agent and one room in it.
room = central_sessions.room


class IdleHub(ReleaseHub):
    """The session host, where a runner can let its idle session go."""

    def __init__(self) -> None:
        super().__init__()
        self.gone = False

    async def open_screen(self, device_id, command, **kw):
        self.gone = False
        return await super().open_screen(device_id, command, **kw)

    async def call_executor(self, device_id, state, method, params, **options):
        if device_id == "center" and self.gone:
            if method == "ping":
                return {"alive": False}
            raise DeviceCallError("the runner let its idle session go")
        return await super().call_executor(device_id, state, method, params, **options)


async def _until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(HANG_S):
        while not condition():
            await asyncio.sleep(0.02)


def _backend(client, hub, tmp_path):
    executor = DeviceChannel(hub=hub, session_factory=client.test_request_factory)
    central: Any = CentralChannel(executor)
    central._device_api_base = AsyncMock(return_value="http://central-api")
    claude = sessions(central)
    claude.bind_owns_sessions(lambda: SimpleNamespace(owns_sessions=True))
    service = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=client.test_request_factory,
        base_system_prompt="System",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([claude], claude.name),
    )
    return service, claude


async def _running_then_let_go(service, claude, hub, project, topic):
    """The session the room's next message goes to, as a turn here would have
    started it, whose runner then let it go after sitting idle; the screen,
    and what has been closed so far."""
    seat = await service._turn_seat_handle(topic)
    session = SessionRef(project, topic, seat, harness=CLAUDE_CODE)
    # Where a turn puts it before writing to it.
    await service._compute.activate(session, claude)
    acting = (await claude.ensure(session, system_prompt="System")).acting
    launch = await service._launch_inputs(topic, seat, acting=acting)
    assert launch is not None
    live = await claude.ensure(
        launch.session,
        system_prompt=launch.system_prompt,
        resume_token=launch.resume_token,
        model=launch.model,
        env=launch.env,
        acting=acting,
        needs_place=launch.needs_place,
    )
    (screen,) = hub.opened
    closed = list(hub.closed)
    # It was talked to, so this process reads it, as after any message.
    claude._listen((topic, seat))
    hub.gone = True
    await _until(lambda: claude.host.answers(live.ref) is False)
    return screen, closed, seat


@pytest.fixture
def idle_room(client, room, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    hub = IdleHub()
    hub.ping = dict(IDLE)
    service, claude = _backend(client, hub, tmp_path)
    app.dependency_overrides[get_chat_service] = lambda: service
    yield room, hub, service, claude
    app.dependency_overrides.pop(get_chat_service, None)


@pytest.mark.anyio
async def test_opening_the_room_starts_the_session_before_any_message(
    client, idle_room
):
    (project, topic), hub, service, claude = idle_room
    old, closed, seat = client.portal.call(
        _running_then_let_go, service, claude, hub, project, topic
    )

    with room_socket(client, topic, "alice"):

        async def started():
            await _until(lambda: old.sid in hub.closed and len(hub.opened) == 1)

        client.portal.call(started)
    assert hub.opened[0].sid != old.sid
    assert hub.closed == [*closed, old.sid]


@pytest.mark.anyio
async def test_a_room_is_looked_at_once_per_window_however_much_is_typed(
    client, idle_room, monkeypatch
):
    (project, topic), hub, service, claude = idle_room
    old, closed, seat = client.portal.call(
        _running_then_let_go, service, claude, hub, project, topic
    )
    room_full = AsyncMock(return_value=False)
    monkeypatch.setattr(service.prewarm._memory, "has_room", room_full)

    with room_socket(client, topic, "alice") as ws:
        # Looked at on opening, while the host had no room for it. The look
        # runs after the socket is accepted, so wait for it rather than for a
        # fixed time: on a busy runner it can take longer than any guess.
        client.portal.call(_until, lambda: room_full.await_count >= 1)
        assert room_full.await_count == 1
        room_full.return_value = True
        for _ in range(5):
            ws.send_json({"type": "typing"})
        # The socket answers frames in order: the pong means all five were read.
        ws.send_json({"type": "ping"})
        while ws.receive_json()["type"] != "pong":
            pass
        client.portal.call(asyncio.sleep, 0.3)
        # Still inside the window: nothing started for all that typing.
        assert room_full.await_count == 1
        assert hub.closed == closed

        # The window has passed. Shortening it stands in for waiting it out,
        # which on the wall clock races however long the reads above took.
        monkeypatch.setattr(prewarm, "ROOM_AGAIN_AFTER_S", 0.0)
        ws.send_json({"type": "typing"})

        async def started():
            await _until(lambda: old.sid in hub.closed and len(hub.opened) == 1)

        client.portal.call(started)
    assert hub.closed == [*closed, old.sid]


@pytest.mark.anyio
async def test_a_full_session_host_starts_nothing(client, idle_room, monkeypatch):
    (project, topic), hub, service, claude = idle_room
    old, closed, seat = client.portal.call(
        _running_then_let_go, service, claude, hub, project, topic
    )
    monkeypatch.setattr(
        service.prewarm._memory, "has_room", AsyncMock(return_value=False)
    )

    with room_socket(client, topic, "alice") as ws:
        ws.send_json({"type": "typing"})
        client.portal.call(asyncio.sleep, 0.5)
    assert hub.closed == closed
    assert [s.sid for s in hub.opened] == [old.sid]
