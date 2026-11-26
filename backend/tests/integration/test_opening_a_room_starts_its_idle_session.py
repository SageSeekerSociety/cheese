"""Room activity brings up a session before the next message needs it.

Opening a room or typing in it brings back the session its runner let go.
Typing to a teammate can also start its first session. A session that runs
on the session host needs room in that host's memory.
"""

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

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
from app.domain.agent.owner_provider import AccountChannel, OwnerChannel
from app.domain.agent_instance import account_connections
from app.domain.agent_instance.models import AgentInstance, OwnAgent
from app.domain.device.login_kind import ConnectionKind
from app.domain.device.models import DeviceClaudeLoginRow
from app.domain.project.models import Project
from app.domain.skill_library import native_skill_doc
from app.domain.topic.models import Topic
from app.domain.user.models import User
from app.main import app
from tests.integration import test_central_room_sessions as central_sessions
from tests.integration.conftest import open_task, room_socket
from tests.integration.test_a_release_restarts_quiet_sessions import IDLE, ReleaseHub
from tests.integration.test_archive_retires_storage import _seed_device
from tests.integration.test_central_room_sessions import sessions
from tests.support.hang import HANG_S
from tests.turn_log import a_topic

#: A project with its agent and one room in it.
room = central_sessions.room


class IdleHub(ReleaseHub):
    """The session host, where a runner can let its idle session go."""

    def __init__(self) -> None:
        super().__init__()
        self.gone = False

    def is_online(self, device_id: str) -> bool:
        return device_id == "owner-machine" or super().is_online(device_id)

    async def open_screen(self, device_id, command, **kw):
        self.gone = False
        return await super().open_screen(device_id, command, **kw)

    async def call_executor(self, device_id, state, method, params, **options):
        if device_id != "executor" and self.gone:
            if method == "ping":
                return {"alive": False}
            raise DeviceCallError("the runner let its idle session go")
        return await super().call_executor(device_id, state, method, params, **options)


async def _until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(HANG_S):
        while not condition():
            await asyncio.sleep(0.02)


def _backend(factory, hub, tmp_path):
    executor = DeviceChannel(hub=hub, session_factory=factory)
    channels: tuple[Any, ...] = (
        CentralChannel(executor),
        OwnerChannel(executor),
        AccountChannel(executor),
    )
    for channel in channels:
        channel._device_api_base = AsyncMock(return_value="http://central-api")
    claude, owned, account = (sessions(channel) for channel in channels)
    for runtime in (claude, owned, account):
        runtime.bind_owns_sessions(lambda: SimpleNamespace(owns_sessions=True))
    service = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=factory,
        base_system_prompt="System",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool(
            [claude], owned={(CLAUDE_CODE, False): owned, (CLAUDE_CODE, True): account}
        ),
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
    launch = await service.turn_preparation.launch_inputs(topic, seat, acting=acting)
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
    service, claude = _backend(client.test_request_factory, hub, tmp_path)
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


@pytest.mark.anyio
async def test_a_session_started_ahead_of_a_turn_carries_the_teammates_skills(
    client, idle_room
):
    """The session started when someone opens the room is the one the next
    turn keeps only if its system prompt is the one that turn renders. A
    teammate that carries a skill reads that skill's guide every turn; a
    session started without it was replaced by the first turn — the start the
    room was opened early to save."""
    (project, topic), _hub, service, _claude = idle_room

    async def carried() -> str:
        async with client.test_factory() as db:
            for agent in await db.scalars(
                select(AgentInstance).where(AgentInstance.project_id == project)
            ):
                agent.configuration = {
                    **(agent.configuration or {}),
                    "skills": ["wolfram"],
                }
            await db.commit()
        seat = await service._turn_seat_handle(topic)
        launch = await service.turn_preparation.launch_inputs(topic, seat, acting=seat)
        assert launch is not None
        return launch.system_prompt

    guide = native_skill_doc("wolfram")
    assert guide is not None
    assert guide[1] in client.portal.call(carried)


@pytest.fixture
def fresh_room(client, room, monkeypatch, tmp_path):
    """A room nobody has written to: its teammate has no session yet."""
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    hub = IdleHub()
    hub.ping = dict(IDLE)
    service, claude = _backend(client.test_request_factory, hub, tmp_path)
    app.dependency_overrides[get_chat_service] = lambda: service
    yield room, hub, service, claude
    app.dependency_overrides.pop(get_chat_service, None)


async def _connect_seat(factory, topic_id, seat, connection):
    async with factory() as db:
        topic = await db.get(Topic, topic_id)
        project = await db.get(Project, topic.project_id)
        agent = await db.scalar(
            select(AgentInstance).where(
                AgentInstance.project_id == project.id, AgentInstance.handle == seat
            )
        )
        owner = await db.scalar(select(User).where(User.username == "u"))
        own = OwnAgent(
            instance_id=agent.id, owner_user_id=owner.id, harness=CLAUDE_CODE
        )
        if connection == "device":
            await _seed_device(db, "owner-machine", project_id=project.id)
            login = DeviceClaudeLoginRow(
                device_id="owner-machine",
                team_id=project.team_id,
                installed=True,
                logged_in=True,
                auth_method="claude.ai",
                checked_at=datetime.now(UTC),
                allowed_models=["sonnet"],
            )
            db.add(login)
            await db.flush()
            own.login_id = login.id
        else:
            account = await account_connections.create(
                db,
                project.team_id,
                kind=ConnectionKind.api_key,
                key="fixture-key",
                base_url=None,
                model=None,
                name=None,
                allowed_models=["sonnet"],
            )
            own.account_connection_id = account.id
        db.add(own)
        await db.commit()


@pytest.mark.anyio
@pytest.mark.parametrize("entry", ["start", "prewarm"])
@pytest.mark.parametrize("connection", ["device", "platform", "account"])
async def test_only_sessions_on_the_session_host_wait_for_memory(
    db_factory, monkeypatch, tmp_path, entry, connection
):
    topic = await a_topic(db_factory)
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    hub = IdleHub()
    hub.ping = dict(IDLE)
    service, _claude = _backend(db_factory, hub, tmp_path)
    monkeypatch.setattr(account_connections, "probe", AsyncMock(return_value=None))
    monkeypatch.setattr(account_connections, "refuse_internal_host", lambda *_: None)
    memory = AsyncMock(return_value=True)
    monkeypatch.setattr(service.prewarm._memory, "has_room", memory)

    async def warm():
        seat = await service._turn_seat_handle(topic)
        if connection != "platform":
            await _connect_seat(db_factory, topic, seat, connection)
        if entry == "start":
            memory.return_value = False
            return await service.prewarm._start(topic, topic, seat)

        assert await service.prewarm._start(topic, topic, seat) == "started"
        runtime = service._compute.seat_runtime(topic, seat)
        live = runtime.live[(topic, seat)]
        runtime._listen((topic, seat))
        hub.gone = True
        await _until(lambda: runtime.host.answers(live.ref) is False)
        memory.return_value = False
        return await service.prewarm._prewarm(live.session)

    expected = {"start": "started", "prewarm": "current"}[entry]
    try:
        outcome = await warm()
    finally:
        await service.stop_listening(0)
    assert outcome == (expected if connection == "device" else "host_full")
    if connection == "device":
        assert [screen.device_id for screen in hub.opened] == ["owner-machine"]
    elif entry == "start":
        assert hub.opened == []
    assert len(hub.closed) == (
        1 if connection == "device" and entry == "prewarm" else 0
    )


@pytest.mark.anyio
async def test_typing_to_a_teammate_starts_its_first_session(client, fresh_room):
    """The first message to a teammate used to wait for its session to start.
    Typing a message that would go to it is the sign that one is coming."""
    (_project, topic), hub, service, claude = fresh_room
    seat = client.portal.call(service._turn_seat_handle, topic)

    with room_socket(client, topic, "alice") as ws:
        ws.send_json({"type": "typing", "topic": str(topic), "agent": seat})
        client.portal.call(_until, lambda: claude.holds(topic, seat))
    assert len(hub.opened) == 1


@pytest.mark.anyio
async def test_typing_to_nobody_starts_no_session(client, fresh_room):
    """Opening a room, or typing to the people in it, starts no session the
    room never had: most rooms that are opened are only read."""
    (_project, topic), hub, _service, claude = fresh_room

    with room_socket(client, topic, "alice") as ws:
        ws.send_json({"type": "typing", "topic": str(topic)})
        ws.send_json({"type": "ping", "topic": str(topic)})
        while ws.receive_json()["type"] != "pong":
            pass
        client.portal.call(asyncio.sleep, 0.3)
    assert hub.opened == []
    assert not claude.holds(topic)


@pytest.mark.anyio
async def test_typing_in_a_task_starts_the_tasks_own_session(client, fresh_room):
    """A task is a conversation of its own in its channel, with a session of
    its own: typing in the task starts that one, not the channel's."""
    (project, topic), hub, service, claude = fresh_room
    task = uuid.UUID(open_task(client, str(topic), owner="alice")["id"])
    seat = client.portal.call(service._turn_seat_handle, task)

    with room_socket(client, task, "alice") as ws:
        ws.send_json({"type": "typing", "topic": str(task), "agent": seat})
        client.portal.call(_until, lambda: claude.holds(task, seat))
    assert not claude.holds(topic, seat)


@pytest.mark.anyio
async def test_a_place_outside_the_watched_room_starts_nothing(client, fresh_room):
    """The typing signal names where the draft is; only a conversation of the
    room being watched is taken from it."""
    (project, topic), hub, service, _claude = fresh_room
    other = uuid.UUID(
        client.post(
            "/topics",
            json={"project_id": str(project), "title": "Elsewhere"},
            headers=central_sessions.session_auth_headers("alice"),
        ).json()["data"]["id"]
    )
    seat = client.portal.call(service._turn_seat_handle, topic)
    outcomes: list[str] = []
    start = service.prewarm._start

    async def recorded(*args):
        outcomes.append(await start(*args))
        return outcomes[-1]

    service.prewarm._start = recorded
    with room_socket(client, topic, "alice") as ws:
        ws.send_json(
            {"type": "typing", "topic": str(topic), "agent": seat, "place": str(other)}
        )
        client.portal.call(_until, lambda: bool(outcomes))
    assert hub.opened == []
