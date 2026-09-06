"""A thread in a Cloud room runs on the room's machine.

A Cloud lease belongs to a ROOM: `project_machines.topic_id` is a foreign key
into `topics`, and a thread is a `tasks` row. Asking the machine service for
"this topic's machine" with a thread id was therefore a 404 on every turn any
thread in a Cloud room ever tried. That 404 is not a named platform failure, so
the runtime retried it three times and then handed the thread over — what the
room saw was 「芝士这轮中断了」 and 「自动续跑」 three times per message, in
every thread, for as long as anyone kept writing.

Every case runs a REAL turn through the production Cloud resolution
(`deps._ensure_topic_cloud` / `deps._read_topic_cloud` — the exact frames of
the traceback), against a machine row for the room that is running, AI-ready
and enrolled, with a connector the hub reports online. The screen is stubbed
the way every turn test stubs it — it supplies the hooks a screen supplies and
nothing else — so what is under test is which machine the turn is sent to and
as whom, not what runs on it.

The identity case is the guard #660 asked for: a thread's conversation is
stored under the thread's OWN agent, so a fix that sent the thread to the
room's machine by simply treating it as the room would run it as the wrong
agent and write its session where nothing reads it back.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.api import deps
from app.core.config import settings
from app.domain.agent.chat import ChatService
from app.domain.agent.cloud_provider import CloudChannel
from app.domain.agent.compute import ComputePool
from app.domain.device.supply import Supply, Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.identity.handles import topic_agent_handle
from app.domain.identity.services import IdentityService
from app.domain.machine.models import AiStatus, MachineStatus
from app.domain.machine.repositories import ProjectMachineRepository
from app.domain.room_task.services import TaskService
from app.domain.topic.repositories import TopicRepository
from tests.conftest import StubChannel, settle_turn


class _Hub:
    """The one fact the hub contributes: whether a machine's connector is attached."""

    def __init__(self, online: set[str]) -> None:
        self.online = online

    def is_online(self, device_id: str) -> bool:
        return device_id in self.online


class _Screen(StubChannel):
    """A stub screen in front of a real Cloud channel.

    The Cloud channel decides WHICH machine and as WHOM; the screen — the part
    every turn test stubs — supplies the hooks. Recording the precheck is what
    makes the channel's decision observable: it is the (device, agent) pair the
    turn was sent to.
    """

    name = "cloud"
    provisions_machine = True

    def __init__(self, cloud: CloudChannel) -> None:
        super().__init__()
        self._cloud = cloud
        self.prechecks: list[tuple[str, int, str]] = []

    async def prepare_topic(self, **kwargs: object) -> tuple[bool, str]:  # type: ignore[override]
        return await self._cloud.prepare_topic(**kwargs)  # type: ignore[arg-type]

    async def precheck(  # type: ignore[override]
        self, project_id: uuid.UUID, topic_id: uuid.UUID
    ) -> tuple[str, int, str]:
        resolved = await self._cloud.precheck(project_id, topic_id)
        self.prechecks.append(resolved)
        return resolved


def _cloud_room(client) -> tuple[str, str, str]:
    """A project, a room on Cloud, and the room's machine — running, AI-ready,
    enrolled as a cloud connector, seen a moment ago so nothing asks MicroCloud.
    Returns (project_id, room_id, the machine's device id)."""
    pid = client.post("/projects", json={"name": "P", "owner_handle": "alice"}).json()[
        "data"
    ]["id"]
    rid = client.post(
        "/topics",
        json={"project_id": pid, "title": "房间", "created_by": "alice"},
    ).json()["data"]["id"]
    made: dict[str, str] = {}

    async def _seed() -> None:
        async with client.test_factory() as s:
            room = await TopicRepository(s).get(uuid.UUID(rid))
            assert room is not None
            room.compute_profile = "cloud"
            owner = await IdentityService(s).ensure_agent_user()
            devices = sql_device_service(s)
            device = await devices.approve(
                await devices.start("room-box"),
                owner_user_id=owner.id,
                supply=Supply.cloud,
                visibility=Visibility.host,
            )
            now = datetime.now(UTC)
            machines = ProjectMachineRepository(s)
            machine = await machines.add(
                project_id=uuid.UUID(pid),
                topic_id=uuid.UUID(rid),
                machine_id=101,
                customer_id=7,
                account_id=9,
                offering_id=1,
                hostname="room-box",
                login_user="cheese",
                cores=2,
                memory_mb=4096,
                disk_gb=20,
                status=MachineStatus.running,
                ip="10.0.1.10",
                requested_by="alice",
                ai_mode="ccproxy",
                ai_status=AiStatus.ready,
                owner_user_id=owner.id,
            )
            await machines.mark_enrolled(machine, device_id=device.device_id, when=now)
            await machines.touch_seen(machine, when=now)
            made["device"] = device.device_id
            await s.commit()

    asyncio.run(_seed())
    return pid, rid, made["device"]


def _thread(client, project_id: str, room_id: str, title: str = "一件活") -> str:
    """A thread nobody has run yet, so the first turn in it is the one under test."""
    made: dict[str, str] = {}

    async def _open() -> None:
        async with client.test_factory() as s:
            task = await TaskService(s).open_thread(
                project_id=uuid.UUID(project_id),
                room_id=uuid.UUID(room_id),
                title=title,
                owner_handle="alice",
                created_by="alice",
                agent_instance_id=None,
            )
            made["id"] = str(task.id)
            await s.commit()

    asyncio.run(_open())
    return made["id"]


def _production_cloud(client, monkeypatch, device_id: str) -> CloudChannel:
    """The Cloud channel as `deps.get_chat_service` wires it, on this test's
    database. The lease functions are the production ones, so a turn goes
    through the same frames as the traceback this test exists for."""
    monkeypatch.setattr(settings, "microcloud_base_url", "https://cloud.example")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "secret")
    monkeypatch.setattr(deps, "async_session_factory", client.test_factory)
    return CloudChannel(
        session_factory=client.test_factory,
        hub=_Hub({device_id}),
        configured=True,
        ensure_topic_cloud=deps._ensure_topic_cloud,
        read_topic_cloud=deps._read_topic_cloud,
    )


def _service(client, tmp_path, screen: _Screen) -> ChatService:
    return ChatService(
        session_factory=client.test_factory,
        compute=ComputePool([screen.runtime], screen.name),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )


def _turn(client, svc: ChatService, place_id: str, content: str = "干活") -> list[str]:
    """Run one turn to its end and return the frame types it produced.
    `converse` returns once the prompt is in the session; the close comes later."""
    pid = uuid.UUID(place_id)
    seen: list[str] = []

    async def _go() -> None:
        async for frame in svc.converse(
            topic_id=pid, author="alice", content=content, summon=True
        ):
            seen.append(frame["type"])
        await settle_turn(svc, pid)

    client.portal.call(_go)
    return seen


def test_a_thread_in_a_cloud_room_runs_on_the_rooms_machine(
    client, tmp_path, monkeypatch
):
    """The turn reaches a screen on the room's machine — no error, no waiting."""
    pid, room, device = _cloud_room(client)
    thread = _thread(client, pid, room)
    screen = _Screen(_production_cloud(client, monkeypatch, device))

    seen = _turn(client, _service(client, tmp_path, screen), thread)

    assert "error" not in seen, seen
    assert "waiting" not in seen, "the room's machine is ready; nothing to wait for"
    assert screen.last_prompt is not None, "the turn never reached a screen"
    assert [sent_to for sent_to, _, _ in screen.prechecks] == [device]


def test_the_thread_runs_as_its_own_agent_not_the_rooms(client, tmp_path, monkeypatch):
    """Same machine as the room, but the thread's own 分身 (#660)."""
    pid, room, device = _cloud_room(client)
    thread = _thread(client, pid, room)
    screen = _Screen(_production_cloud(client, monkeypatch, device))

    _turn(client, _service(client, tmp_path, screen), thread)

    [(_, _, handle)] = screen.prechecks
    assert handle == topic_agent_handle(uuid.UUID(thread))
    assert handle != topic_agent_handle(uuid.UUID(room))


def test_the_room_itself_still_runs_on_its_machine(client, tmp_path, monkeypatch):
    """The room's own path was fine before; resolving a thread through the
    room must not change where the room goes or who it runs as."""
    pid, room, device = _cloud_room(client)
    screen = _Screen(_production_cloud(client, monkeypatch, device))

    seen = _turn(client, _service(client, tmp_path, screen), room)

    assert "error" not in seen, seen
    [(sent_to, _, handle)] = screen.prechecks
    assert sent_to == device
    assert handle == topic_agent_handle(uuid.UUID(room))
