"""A session can work on a whole cloud VM of its own (#2320, disposable × whole
machine).

The room picks it as its work computer; each session in the room then gets a
virtual machine nobody else is placed on, its executor runs there without a
sandbox, and the VM goes as soon as the session is done with it: when the room
moves off it, when the room's cleanup removed its directory, or when the
session has been idle, but never before its work is pushed. Users see the
environment, never the machine.
"""

import ast
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent.models import AgentTurn
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.models import Block, BlockKind
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.machine import cloud_vm
from app.domain.machine import session_work as work_lease
from app.domain.machine.models import (
    HOST_OWNER,
    AiStatus,
    CloudHost,
    CloudHostHome,
    MachineStatus,
)
from app.domain.machine.repositories import CloudHostRepository
from app.domain.machine.services import HostPool
from app.domain.topic.models import Topic
from tests.executor_release import running
from tests.integration.conftest import post_project, session_auth_headers
from tests.microcloud import OFFERING, FakeMicroCloud

LXC = {**OFFERING, "id": 1, "machineTypeName": "standard-lxc"}
VM = {
    **OFFERING,
    "id": 2,
    "machineTypeName": "standard-vm",
    "coresMax": 32,
    "memoryMbMax": 131072,
    "diskGbMax": 128,
}
SANDBOX = {"name": None, "profile": "cloud", "device_id": None}
WHOLE_VM = {**SANDBOX, "whole_machine": True}
PUSHED = {"value": {"stdout": "", "stderr": "", "interrupted": False}}


@pytest.fixture
def cloud(client, monkeypatch):
    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    monkeypatch.setattr(settings, "microcloud_offering_id", LXC["id"])
    monkeypatch.setattr(settings, "microcloud_vm_offering_id", VM["id"])
    monkeypatch.setattr(settings, "microcloud_default_cores", 1)
    monkeypatch.setattr(settings, "cloud_host_slots_per_core", 1)
    monkeypatch.setattr(settings, "cloud_pool_min_free_slots", 0)
    # A pool host is kept this long once empty; a VM is not.
    monkeypatch.setattr(settings, "cloud_host_idle_hold_s", 3600)
    monkeypatch.setattr(settings, "cloud_pool_max_hosts", 20)
    provider = FakeMicroCloud(offerings=[LXC, VM])
    monkeypatch.setattr(
        "app.domain.machine.services.MicroCloudClient", lambda: provider
    )
    project = post_project(client, json={"name": "VMs"}, owner="alice").json()["data"]
    online: set[str] = set()

    async def install(device, argv, **kwargs):
        return {
            "exit": 0,
            "stdout": json.dumps(
                {
                    "state": f"/{device}/state",
                    "workspace": f"/{device}/work",
                    "mcp_servers": [],
                }
            ),
        }

    hub = SimpleNamespace(
        is_online=lambda device: device in online,
        reconnecting=lambda device: False,
        exec=AsyncMock(side_effect=install),
    )
    monkeypatch.setattr(work_lease, "device_hub", hub)
    calls = AsyncMock(
        side_effect=lambda lease, method, *a, **k: (
            running() if method == "ping" else PUSHED
        )
    )
    monkeypatch.setattr(execution, "call", calls)
    return SimpleNamespace(
        client=client,
        provider=provider,
        project_id=uuid.UUID(project["id"]),
        hub=hub,
        online=online,
        calls=calls,
    )


def _room(case, choice: dict, title: str = "Room"):
    """A room working on ``choice``, with one agent session that has not run
    anything yet."""
    room = uuid.UUID(
        case.client.post(
            "/topics",
            json={"project_id": str(case.project_id), "title": title},
            headers=session_auth_headers("alice"),
        ).json()["data"]["id"]
    )

    async def seed():
        async with case.client.test_request_factory() as db:
            topic = await db.get(Topic, room)
            topic.compute_config = choice
            agent = await IdentityService(db).ensure_room_agent_user(room)
            row = await AgentSessionService(db).ensure(
                room, "cheese", harness="claude-code"
            )
            resource = str(topic.resource_id or room)
            row.runtime_location = {
                "device_id": "center",
                "resource_id": resource,
                "channel": "cloud",
            }
            token = bind_resource_token(
                mint_scoped_token(
                    project_id=str(case.project_id),
                    topic_id=str(room),
                    agent_handle=agent.username,
                ),
                resource,
                session_id=str(row.id),
            )
            await db.commit()
            return SimpleNamespace(room=room, session=row.id, token=token)

    return case.client.portal.call(seed)


def _lease(case, seat, timeout=660):
    return case.client.post(
        f"/topics/{seat.room}/sessions/{seat.session}/work-lease",
        headers={"X-Cheese-Token": seat.token},
        json={"env": {}, "timeout": timeout},
    )


def _host(case, seat) -> CloudHost | None:
    async def read():
        async with case.client.test_request_factory() as db:
            repo = CloudHostRepository(db)
            home = await repo.current_home(seat.session)
            return None if home is None else await repo.get(home.host_id)

    return case.client.portal.call(read)


def _up(case, seat) -> str:
    """The session's machine came up, and the sweep enrolled it."""

    async def enrol():
        async with case.client.test_request_factory() as db:
            owner = await IdentityService(db).ensure_agent_user(handle=HOST_OWNER)
            devices = sql_device_service(db)
            device = await devices.approve(
                await devices.start("pool"), owner_user_id=owner.id, supply=Supply.cloud
            )
            repo = CloudHostRepository(db)
            home = await repo.current_home(seat.session)
            host = await repo.get(home.host_id)
            case.provider.machines[host.machine_id].update(
                status="running", aiStatus="disabled"
            )
            host.status = MachineStatus.running
            host.ai_status = AiStatus.disabled
            host.last_seen_at = datetime.now(UTC)
            await repo.mark_enrolled(
                host, device_id=device.device_id, when=datetime.now(UTC)
            )
            await db.commit()
            return device.device_id

    device_id = case.client.portal.call(enrol)
    case.online.add(device_id)
    return device_id


def _working(case, seat) -> tuple[str, dict]:
    """The session's VM is up and its executor installed."""
    assert _lease(case, seat, timeout=0.001).json()["data"]["preparing"]
    device_id = _up(case, seat)
    answer = _lease(case, seat)
    assert answer.status_code == 200, answer.text
    target = answer.json()["data"]["target"]
    assert target["device_id"] == device_id
    return device_id, target


def _room_lines(case, seat) -> list[str]:
    async def read():
        async with case.client.test_request_factory() as db:
            rows = await db.scalars(
                select(Block.content)
                .where(
                    Block.topic_id == seat.room,
                    Block.kind == BlockKind.event,
                    Block.meta["event_type"]
                    .as_string()
                    .in_(["cloud_startup", "cloud_provisioning"]),
                )
                .order_by(Block.created_at)
            )
            return list(rows)

    return case.client.portal.call(read)


def _sweep(case):
    async def go():
        async with case.client.test_request_factory() as db:
            await HostPool(db, case.provider).maintain()
            await db.commit()

    case.client.portal.call(go)


def _release_idle(case) -> int:
    async def go():
        async with case.client.test_request_factory() as db:
            return await cloud_vm.release_idle(db)

    return case.client.portal.call(go)


def _idle_for(case, seat, delta: timedelta):
    async def go():
        async with case.client.test_request_factory() as db:
            await db.execute(
                update(CloudHostHome)
                .where(CloudHostHome.session_id == seat.session)
                .values(active_at=datetime.now(UTC) - delta)
            )
            await db.commit()

    case.client.portal.call(go)


def _session_lease(case, seat):
    async def go():
        async with case.client.test_request_factory() as db:
            return (await AgentSessionService(db).by_id(seat.session)).work_lease

    return case.client.portal.call(go)


def _pushed_on(case, device_id) -> bool:
    return any(
        call.args[0]["device_id"] == device_id
        and call.args[1] == "control"
        and call.args[2]["subtype"] == "checkpoint"
        for call in case.calls.await_args_list
    )


# --- choosing it -------------------------------------------------------------


def test_a_room_can_choose_a_whole_cloud_vm(cloud):
    case = cloud
    room = case.client.post(
        "/topics",
        json={"project_id": str(case.project_id), "title": "Docker work"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    path = f"/topics/{room}/compute-profile"
    headers = session_auth_headers("alice")

    offered = case.client.get(path, headers=headers).json()["data"]
    assert offered["cloud_vm_available"] is True

    chosen = case.client.put(path, headers=headers, json={"choice": WHOLE_VM})
    assert chosen.status_code == 200, chosen.text
    now = case.client.get(path, headers=headers).json()["data"]
    assert now["choice"]["profile"] == "cloud"
    assert now["choice"]["whole_machine"] is True

    # What the agent's own `cheese_machine` sends: a profile and the flag.
    back = case.client.put(path, headers=headers, json={"profile": "cloud"})
    assert back.status_code == 200, back.text
    assert back.json()["data"]["choice"]["whole_machine"] is False
    again = case.client.put(
        path, headers=headers, json={"profile": "cloud", "whole_machine": True}
    )
    assert again.status_code == 200, again.text
    assert again.json()["data"]["choice"]["whole_machine"] is True


def test_a_whole_vm_is_not_offered_where_no_vm_offering_is_set(cloud, monkeypatch):
    case = cloud
    monkeypatch.setattr(settings, "microcloud_vm_offering_id", 0)
    room = case.client.post(
        "/topics",
        json={"project_id": str(case.project_id), "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    path = f"/topics/{room}/compute-profile"
    headers = session_auth_headers("alice")

    assert (
        case.client.get(path, headers=headers).json()["data"]["cloud_vm_available"]
        is False
    )
    refused = case.client.put(path, headers=headers, json={"choice": WHOLE_VM})
    assert refused.status_code in (400, 422), refused.text
    assert "云虚拟机" in refused.text
    on_device = case.client.put(
        path,
        headers=headers,
        json={"choice": {"profile": "device", "whole_machine": True}},
    )
    assert on_device.status_code in (400, 422), on_device.text


def test_the_project_default_can_be_a_whole_vm(cloud):
    case = cloud
    path = f"/projects/{case.project_id}/compute-configs"
    headers = session_auth_headers("alice")
    assert (
        case.client.get(path, headers=headers).json()["data"]["cloud_vm_available"]
        is True
    )

    saved = case.client.put(path, headers=headers, json={"default": WHOLE_VM})

    assert saved.status_code == 200, saved.text
    assert (
        case.client.get(path, headers=headers).json()["data"]["default"][
            "whole_machine"
        ]
        is True
    )


# --- provisioning ------------------------------------------------------------


def test_each_session_on_a_whole_vm_gets_a_machine_of_its_own(cloud):
    case = cloud
    first, second = _room(case, WHOLE_VM, "A"), _room(case, WHOLE_VM, "B")
    sandboxed = _room(case, SANDBOX, "C")

    for seat in (first, second):
        waiting = _lease(case, seat, timeout=0.001)
        assert waiting.status_code == 200, waiting.text
        assert waiting.json()["data"]["preparing"] is True
        assert waiting.json()["data"]["unavailable"].startswith("云虚拟机正在准备")
    assert _lease(case, sandboxed, timeout=0.001).json()["data"]["preparing"]

    vms = [body for body in case.provider.created if body["offeringId"] == VM["id"]]
    hosts = [body for body in case.provider.created if body["offeringId"] == LXC["id"]]
    assert len(vms) == 2 and len(hosts) == 1
    for body in vms:
        assert (body["cores"], body["memoryMb"], body["diskGb"]) == (
            settings.cloud_vm_cores,
            settings.cloud_vm_memory_mb,
            settings.cloud_vm_disk_gb,
        )
        assert body["aiMode"] == "none"
    # Under the platform's own account, like every host of the pool.
    assert {body["customerId"] for body in case.provider.created} == {7}
    placed = {_host(case, seat).id for seat in (first, second, sandboxed)}
    assert len(placed) == 3
    assert _host(case, first).whole_machine and not _host(case, sandboxed).whole_machine
    assert _host(case, first).project_id == case.project_id
    assert _room_lines(case, first) == ["正在准备云虚拟机"]
    assert _room_lines(case, sandboxed) == ["正在准备沙箱"]


def test_a_sandbox_is_never_placed_on_a_vm_and_a_vm_never_on_a_host(cloud):
    case = cloud
    on_vm = _room(case, WHOLE_VM, "VM")
    _working(case, on_vm)
    # A pool host with a free slot, up and enrolled.
    boxed = _room(case, SANDBOX, "Sandbox")
    _working(case, boxed)
    created = len(case.provider.created)

    later_vm = _room(case, WHOLE_VM, "Later VM")
    assert _lease(case, later_vm, timeout=0.001).json()["data"]["preparing"]
    assert len(case.provider.created) == created + 1
    assert _host(case, later_vm).whole_machine
    assert _host(case, later_vm).id not in {
        _host(case, on_vm).id,
        _host(case, boxed).id,
    }


def test_a_whole_vms_executor_runs_without_a_sandbox(cloud):
    case = cloud
    seat = _room(case, WHOLE_VM)

    device_id, _target = _working(case, seat)

    [installed] = [
        call.kwargs["stdin"]
        for call in case.hub.exec.await_args_list
        if call.args[0] == device_id
    ]
    configure = ast.parse(installed).body[-1].value
    payload = json.loads(ast.literal_eval(configure.args[0].args[0]))
    assert payload["sandbox"] is None
    assert _room_lines(case, seat) == ["正在准备云虚拟机", "云虚拟机已就绪"]


def test_users_see_the_environment_and_never_the_vm(cloud):
    case = cloud
    seat = _room(case, WHOLE_VM)
    device_id, target = _working(case, seat)
    host = _host(case, seat)

    answer = case.client.get(
        f"/topics/{seat.room}/compute-profile", headers=session_auth_headers("alice")
    )

    assert answer.status_code == 200, answer.text
    [session] = answer.json()["data"]["sessions"]
    assert session["lease"] == {"status": "ready", "online": True}
    assert session["choice"]["whole_machine"] is True
    # The whole machine is the session's: what it sees is all of it.
    assert session["machine_access"] is True
    for secret in (device_id, host.hostname, str(host.machine_id), target["home"]):
        assert secret not in answer.text
    distribution = case.client.get(
        f"/projects/{case.project_id}/compute-configs",
        headers=session_auth_headers("alice"),
    ).json()["data"]["distribution"]
    assert distribution["cloud_vm"] == 1 and distribution["cloud"] == 0
    assert device_id not in json.dumps(distribution)


def test_whole_vms_count_against_the_platforms_cap(cloud, monkeypatch):
    case = cloud
    monkeypatch.setattr(settings, "cloud_pool_max_hosts", 1)
    first, second = _room(case, WHOLE_VM, "A"), _room(case, WHOLE_VM, "B")
    assert _lease(case, first, timeout=0.001).json()["data"]["preparing"]

    refused = _lease(case, second, timeout=5).json()["data"]

    assert "preparing" not in refused
    assert "暂时无法准备云虚拟机" in refused["unavailable"]
    assert len(case.provider.created) == 1


# --- release -----------------------------------------------------------------


def test_a_vm_goes_once_its_room_moves_off_it_after_a_push(cloud):
    case = cloud
    seat = _room(case, WHOLE_VM)
    device_id, _ = _working(case, seat)
    machine = _host(case, seat).machine_id

    moved = case.client.put(
        f"/topics/{seat.room}/compute-profile",
        headers=session_auth_headers("alice"),
        json={"choice": SANDBOX},
    )

    assert moved.status_code == 200, moved.text
    assert _pushed_on(case, device_id)
    assert _host(case, seat) is None
    _sweep(case)
    # No idle hold for a VM: nobody else will ever be placed on it.
    assert case.provider.deleted == [machine]


def test_a_vm_left_without_a_push_keeps_its_work(cloud):
    case = cloud
    seat = _room(case, WHOLE_VM)
    device_id, _ = _working(case, seat)
    machine = _host(case, seat).machine_id
    case.online.discard(device_id)

    moved = case.client.put(
        f"/topics/{seat.room}/compute-profile",
        headers=session_auth_headers("alice"),
        json={"choice": SANDBOX, "abandon_unpushed": True},
    )

    assert moved.status_code == 200, moved.text
    _sweep(case)
    assert case.provider.deleted == []

    async def homes():
        async with case.client.test_request_factory() as db:
            return list(
                await db.scalars(
                    select(CloudHostHome).where(
                        CloudHostHome.session_id == seat.session
                    )
                )
            )

    [kept] = case.client.portal.call(homes)
    assert kept.left_at is not None
    assert machine in case.provider.machines


def test_a_vm_goes_once_the_rooms_cleanup_removed_its_directory(cloud):
    case = cloud
    seat = _room(case, WHOLE_VM)
    device_id, target = _working(case, seat)
    machine = _host(case, seat).machine_id

    async def cleaned():
        async with case.client.test_request_factory() as db:
            await HostPool(db, case.provider).forget_device_homes(
                device_id, target["resource_id"]
            )
            await db.commit()

    case.client.portal.call(cleaned)
    _sweep(case)

    assert case.provider.deleted == [machine]


def test_an_idle_vm_is_pushed_and_released(cloud):
    case = cloud
    seat = _room(case, WHOLE_VM)
    device_id, _ = _working(case, seat)
    machine = _host(case, seat).machine_id
    _idle_for(case, seat, timedelta(seconds=settings.cloud_vm_idle_release_s + 60))

    assert _release_idle(case) == 1

    assert _pushed_on(case, device_id)
    assert _session_lease(case, seat) is None
    assert _host(case, seat) is None
    minutes = settings.cloud_vm_idle_release_s // 60
    assert _room_lines(case, seat)[-1].startswith(f"云虚拟机 {minutes} 分钟没有活动")
    _sweep(case)
    assert case.provider.deleted == [machine]

    # The session's next tool call prepares a new one.
    assert _lease(case, seat, timeout=0.001).json()["data"]["preparing"]
    assert _host(case, seat).machine_id not in (None, machine)


def test_a_vm_is_kept_while_its_session_or_room_is_at_work(cloud):
    case = cloud
    seat = _room(case, WHOLE_VM)
    _working(case, seat)
    idle = timedelta(seconds=settings.cloud_vm_idle_release_s + 60)

    # Used a minute ago.
    _idle_for(case, seat, timedelta(minutes=1))
    assert _release_idle(case) == 0

    # Idle as a session, but a turn is running in its room.
    _idle_for(case, seat, idle)

    async def turn():
        async with case.client.test_request_factory() as db:
            db.add(
                AgentTurn(
                    id=uuid.uuid4(),
                    topic_id=seat.room,
                    continuation_id=uuid.uuid4(),
                    author="alice",
                    started_at=datetime.now(UTC) - timedelta(hours=1),
                )
            )
            await db.commit()

    case.client.portal.call(turn)
    assert _release_idle(case) == 0
    assert _host(case, seat) is not None
    assert case.provider.deleted == []


def test_an_idle_vm_whose_push_fails_is_kept_with_its_work(cloud, monkeypatch):
    case = cloud
    seat = _room(case, WHOLE_VM)
    device_id, _ = _working(case, seat)
    _idle_for(case, seat, timedelta(seconds=settings.cloud_vm_idle_release_s + 60))
    monkeypatch.setattr(cloud_vm, "_push_failed", {})
    case.calls.side_effect = lambda lease, method, *a, **k: (
        running() if method == "ping" else {"error": "remote rejected the push"}
    )

    assert _release_idle(case) == 0

    assert _pushed_on(case, device_id)
    assert _host(case, seat) is not None
    assert _session_lease(case, seat)["device_id"] == device_id
    _sweep(case)
    assert case.provider.deleted == []
    # Not asked again on the very next sweep.
    case.calls.reset_mock()
    assert _release_idle(case) == 0
    assert not _pushed_on(case, device_id)
