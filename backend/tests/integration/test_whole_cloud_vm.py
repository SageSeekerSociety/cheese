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
import re
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, update

from app.core.config import settings
from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent.models import AgentTurn
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.machine import cloud_vm
from app.domain.machine import session_work as work_lease
from app.domain.machine.lifecycle import SandboxLifecycle
from app.domain.machine.models import (
    HOST_OWNER,
    AiStatus,
    CloudHost,
    MachineStatus,
    SandboxHome,
)
from app.domain.machine.repositories import CloudHostRepository
from app.domain.machine.runner import ComputeMeterSweeper
from app.domain.machine.services import HostPool
from app.domain.run_record.models import RunRecord
from app.domain.topic.models import Topic, TopicStatus
from app.domain.usage.models import ComputeRun, ResourceUsage
from tests.executor_release import running
from tests.integration.conftest import open_task, post_project, session_auth_headers
from tests.microcloud import OFFERING, FakeMicroCloud
from tests.support.devices import enrollment_team

LXC = {**OFFERING, "id": 1, "machineTypeName": "standard-lxc"}
VM = {
    **OFFERING,
    "id": 2,
    "machineTypeName": "standard-vm",
    "coresMax": 32,
    "memoryMbMax": 131072,
    "diskGbMax": 128,
}
SANDBOX = {"kind": "sandbox", "spec": "standard"}
WHOLE_VM = {"kind": "sandbox", "spec": "vm"}
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
    # The pool here is sized by demand; the VMs take the room the cap leaves.
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 0)
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


def _room(case, environment: dict, title: str = "Room"):
    """A room whose new agents get ``environment``, with one agent session that
    has not run anything yet."""
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
            topic.default_environment = environment
            agent = await IdentityService(db).ensure_room_agent_user(room)
            row = await AgentSessionService(db).ensure(
                room, "cheese", harness="claude-code"
            )
            resource = str(topic.resource_id or room)
            row.runtime_location = {
                "device_id": "center",
                "resource_id": resource,
                "channel": "device",
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
        headers={"Authorization": f"Bearer {seat.token}"},
        json={"env": {}, "timeout": timeout},
    )


def _host(case, seat) -> CloudHost | None:
    async def read():
        async with case.client.test_request_factory() as db:
            repo = CloudHostRepository(db)
            home = await repo.current_home(seat.session)
            return None if home is None else await repo.get(home.host_id)

    return case.client.portal.call(read)


def _homes_on(case, host_id) -> int:
    """How many homes are left on that host. A home whose ``session_id`` was
    cleared is no longer the one ``current_home`` finds, so after that the
    host is what is left to ask by."""

    async def read():
        async with case.client.test_request_factory() as db:
            return await db.scalar(
                select(func.count())
                .select_from(SandboxHome)
                .where(SandboxHome.host_id == host_id)
            )

    return case.client.portal.call(read)


def _up(case, seat) -> str:
    """The session's machine came up, and the sweep enrolled it."""

    async def enrol():
        async with case.client.test_request_factory() as db:
            owner = await IdentityService(db).ensure_agent_user(handle=HOST_OWNER)
            devices = sql_device_service(db)
            device = await devices.approve(
                await devices.start("pool"),
                owner_user_id=owner.id,
                supply=Supply.cloud,
                team_id=await enrollment_team(devices, owner.id),
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
                host,
                device_id=device.device_id,
                when=datetime.now(UTC),
                sandbox_disk_gb=None,
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
                select(RunRecord.content)
                .where(
                    RunRecord.conversation_id == seat.room,
                    RunRecord.kind.in_(["cloud_startup", "cloud_provisioning"]),
                )
                .order_by(RunRecord.created_at)
            )
            return list(rows)

    return case.client.portal.call(read)


def _leaked(text: str, secret: str) -> bool:
    """Whether ``secret`` shows up in ``text`` as a token of its own.

    Plain containment also matches digits inside a longer token, and the
    machine's number is three digits: the session uuid ``b101e7f6-…`` carries
    ``101``, as does any number that happens to end that way, often enough to
    fail a run that disclosed nothing. Both a uuid's characters and a number's
    are hex digits, so requiring that both neighbours be something else keeps
    a real disclosure — the number as a value — failing, and drops these
    coincidences.
    """
    return (
        re.search(rf"(?<![0-9a-fA-F]){re.escape(secret)}(?![0-9a-fA-F])", text)
        is not None
    )


def _sweep(case):
    async def go():
        async with case.client.test_request_factory() as db:
            await HostPool(db, case.provider).maintain()
            await db.commit()

    case.client.portal.call(go)


def _release_idle(case) -> int:
    """The sandbox sweep's turn at idle VMs."""

    async def go():
        async with case.client.test_request_factory() as db:
            return await cloud_vm.release_idle(db, SandboxLifecycle(db, hub=case.hub))

    return case.client.portal.call(go)


def _idle_for(case, seat, delta: timedelta):
    async def go():
        async with case.client.test_request_factory() as db:
            await db.execute(
                update(SandboxHome)
                .where(SandboxHome.session_id == seat.session)
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


def test_a_projects_settings_say_what_each_sandbox_spec_is_and_costs(cloud):
    """Where a project picks its default environment is where a reader learns
    what each spec on offer is and what it costs: a spec named with nothing
    beside it leaves the choice to a guess."""
    case = cloud
    specs = case.client.get(
        f"/projects/{case.project_id}/compute-configs",
        headers=session_auth_headers("alice"),
    ).json()["data"]["specs"]
    assert {"standard", "vm"} <= {spec["spec"] for spec in specs}
    about = [spec["description"] for spec in specs]
    assert all(about) and len(set(about)) == len(about)
    # Said in the reader's language: the sentence's key travels beside it.
    assert all(spec["i18n"]["description"]["key"] for spec in specs)


def test_a_room_can_give_its_new_agents_a_vm_sandbox(cloud):
    case = cloud
    room = case.client.post(
        "/topics",
        json={"project_id": str(case.project_id), "title": "Docker work"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    path = f"/topics/{room}/environment"
    headers = session_auth_headers("alice")

    offered = case.client.get(path, headers=headers).json()["data"]
    assert {"standard", "vm"} <= {spec["spec"] for spec in offered["specs"]}

    chosen = case.client.put(
        f"/topics/{room}/environment-default",
        headers=headers,
        json={"environment": WHOLE_VM},
    )
    assert chosen.status_code == 200, chosen.text
    now = case.client.get(path, headers=headers).json()["data"]
    assert (now["default"], now["default_from"]) == (WHOLE_VM, "room")


def test_a_vm_sandbox_is_not_offered_where_no_vm_offering_is_set(cloud, monkeypatch):
    case = cloud
    monkeypatch.setattr(settings, "microcloud_vm_offering_id", 0)
    room = case.client.post(
        "/topics",
        json={"project_id": str(case.project_id), "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    headers = session_auth_headers("alice")

    offered = case.client.get(f"/topics/{room}/environment", headers=headers)
    assert "vm" not in {spec["spec"] for spec in offered.json()["data"]["specs"]}
    refused = case.client.put(
        f"/topics/{room}/environment-default",
        headers=headers,
        json={"environment": WHOLE_VM},
    )
    assert refused.status_code in (400, 422), refused.text


def test_the_project_default_can_be_a_vm_sandbox(cloud):
    case = cloud
    path = f"/projects/{case.project_id}/compute-configs"
    headers = session_auth_headers("alice")
    offered = case.client.get(path, headers=headers).json()["data"]["specs"]
    assert "vm" in {spec["spec"] for spec in offered}

    saved = case.client.put(path, headers=headers, json={"default": WHOLE_VM})

    assert saved.status_code == 200, saved.text
    assert case.client.get(path, headers=headers).json()["data"]["default"] == WHOLE_VM


# --- provisioning ------------------------------------------------------------


def test_each_session_on_a_whole_vm_gets_a_machine_of_its_own(cloud):
    case = cloud
    first, second = _room(case, WHOLE_VM, "A"), _room(case, WHOLE_VM, "B")
    sandboxed = _room(case, SANDBOX, "C")

    for seat in (first, second):
        waiting = _lease(case, seat, timeout=0.001)
        assert waiting.status_code == 200, waiting.text
        assert waiting.json()["data"]["preparing"] is True
        assert waiting.json()["data"]["unavailable"].startswith("虚拟机沙箱正在准备")
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
    assert _room_lines(case, first) == ["正在准备云端虚拟机"]
    assert _room_lines(case, sandboxed) == ["正在准备环境"]


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
    assert _room_lines(case, seat) == ["正在准备云端虚拟机", "云端虚拟机已就绪"]


def test_users_see_the_environment_and_never_the_vm(cloud):
    case = cloud
    seat = _room(case, WHOLE_VM)
    device_id, target = _working(case, seat)
    host = _host(case, seat)

    answer = case.client.get(
        f"/topics/{seat.room}/environment", headers=session_auth_headers("alice")
    )

    assert answer.status_code == 200, answer.text
    [session] = answer.json()["data"]["sessions"]
    assert (session["status"], session["online"]) == ("ready", True)
    assert session["environment"] == WHOLE_VM
    for secret in (device_id, host.hostname, str(host.machine_id), target["home"]):
        assert not _leaked(answer.text, secret)
    distribution = case.client.get(
        f"/projects/{case.project_id}/compute-configs",
        headers=session_auth_headers("alice"),
    ).json()["data"]["distribution"]
    assert [(e["spec"], e["agents"]) for e in distribution["sandboxes"]] == [("vm", 1)]
    assert device_id not in json.dumps(distribution)


def test_whole_vms_count_against_the_platforms_cap(cloud, monkeypatch):
    case = cloud
    monkeypatch.setattr(settings, "cloud_pool_max_hosts", 1)
    first, second = _room(case, WHOLE_VM, "A"), _room(case, WHOLE_VM, "B")
    assert _lease(case, first, timeout=0.001).json()["data"]["preparing"]

    refused = _lease(case, second, timeout=5).json()["data"]

    assert "preparing" not in refused
    assert "暂时无法准备虚拟机沙箱" in refused["unavailable"]
    assert len(case.provider.created) == 1


# --- release -----------------------------------------------------------------


def test_a_vm_goes_once_its_room_moves_off_it_after_a_push(cloud):
    case = cloud
    seat = _room(case, WHOLE_VM)
    device_id, _ = _working(case, seat)
    machine = _host(case, seat).machine_id

    moved = case.client.put(
        f"/topics/{seat.room}/sessions/{seat.session}/environment",
        headers=session_auth_headers("alice"),
        json={"environment": SANDBOX},
    )

    assert moved.status_code == 200, moved.text
    assert _pushed_on(case, device_id)
    assert _host(case, seat) is None
    _sweep(case)
    # No idle hold for a VM: nobody else will ever be placed on it.
    assert case.provider.deleted == [machine]


def test_a_vm_left_when_it_cannot_be_reached_goes_all_the_same(cloud):
    case = cloud
    seat = _room(case, WHOLE_VM)
    device_id, _ = _working(case, seat)
    machine = _host(case, seat).machine_id
    case.online.discard(device_id)

    moved = case.client.put(
        f"/topics/{seat.room}/sessions/{seat.session}/environment",
        headers=session_auth_headers("alice"),
        json={"environment": SANDBOX},
    )

    assert moved.status_code == 200, moved.text
    assert not _pushed_on(case, device_id)
    assert _host(case, seat) is None
    _sweep(case)
    assert case.provider.deleted == [machine]


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
    assert _room_lines(case, seat)[-1].startswith(f"云端虚拟机 {minutes} 分钟没有活动")
    _sweep(case)
    assert case.provider.deleted == [machine]

    # The session's next tool call prepares a new one.
    assert _lease(case, seat, timeout=0.001).json()["data"]["preparing"]
    assert _host(case, seat).machine_id not in (None, machine)


def test_recovering_vms_do_not_hide_an_unrelated_idle_vm(cloud):
    case = cloud
    seats = [_room(case, WHOLE_VM) for _ in range(cloud_vm.PER_SWEEP + 1)]
    for index, seat in enumerate(seats):
        _working(case, seat)
        _idle_for(
            case,
            seat,
            timedelta(seconds=settings.cloud_vm_idle_release_s + 600 - index * 60),
        )
    pending = {seat.room for seat in seats[:-1]}

    async def sweep():
        async with case.client.test_request_factory() as db:
            lifecycle = SandboxLifecycle(
                db,
                hub=case.hub,
                recovering=lambda conversation: conversation in pending,
            )
            return await cloud_vm.release_idle(db, lifecycle)

    assert case.client.portal.call(sweep) == 1
    assert all(_host(case, seat) is not None for seat in seats[:-1])
    assert _host(case, seats[-1]) is None


def test_a_background_command_keeps_a_vm_past_its_idle_release(cloud, monkeypatch):
    """A VM's keep-alive is its own and longer than a sandbox's. A command the
    executor still runs for the session holds the VM past the idle release that
    would otherwise let it go — the shared sandbox cap is shorter than that
    release, so reading it here would release the VM under the command."""
    case = cloud
    seat = _room(case, WHOLE_VM)
    _working(case, seat)
    monkeypatch.setattr(
        execution,
        "call",
        AsyncMock(
            side_effect=lambda lease, method, *a, **k: (
                {**running(), "running_commands": 1} if method == "ping" else PUSHED
            )
        ),
    )
    _idle_for(case, seat, timedelta(seconds=settings.cloud_vm_idle_release_s + 60))

    assert _release_idle(case) == 0
    assert _host(case, seat) is not None


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
                    conversation_id=seat.room,
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


def test_a_vm_goes_while_another_task_of_its_room_is_at_work(cloud):
    """A room's tasks are not its sessions' work: the VM of a session that
    works in the room itself goes while a task of the room runs, and comes
    back, new, on the session's next tool call."""
    case = cloud
    seat = _room(case, WHOLE_VM)
    device_id, _ = _working(case, seat)
    machine = _host(case, seat).machine_id
    task = open_task(case.client, str(seat.room), "另一件活")
    _idle_for(case, seat, timedelta(seconds=settings.cloud_vm_idle_release_s + 60))

    async def turn():
        async with case.client.test_request_factory() as db:
            db.add(
                AgentTurn(
                    id=uuid.uuid4(),
                    conversation_id=uuid.UUID(task["id"]),
                    continuation_id=uuid.uuid4(),
                    author="alice",
                    started_at=datetime.now(UTC) - timedelta(minutes=1),
                )
            )
            await db.commit()

    case.client.portal.call(turn)

    assert _release_idle(case) == 1

    assert _pushed_on(case, device_id)
    assert _host(case, seat) is None
    _sweep(case)
    assert case.provider.deleted == [machine]


def test_an_archived_rooms_idle_vm_goes(cloud):
    """Nobody unarchives a room to release its VM: the sweep does."""
    case = cloud
    seat = _room(case, WHOLE_VM)
    device_id, _ = _working(case, seat)
    machine = _host(case, seat).machine_id
    _idle_for(case, seat, timedelta(seconds=settings.cloud_vm_idle_release_s + 60))

    async def archive():
        async with case.client.test_request_factory() as db:
            await db.execute(
                update(Topic)
                .where(Topic.id == seat.room)
                .values(status=TopicStatus.archived)
            )
            await db.commit()

    case.client.portal.call(archive)

    assert _release_idle(case) == 1

    assert _pushed_on(case, device_id)
    assert _host(case, seat) is None
    _sweep(case)
    assert case.provider.deleted == [machine]


def test_a_vm_whose_session_walked_away_goes(cloud):
    """A home no session holds is nobody else's to release: the sweep's."""
    case = cloud
    seat = _room(case, WHOLE_VM)
    device_id, _ = _working(case, seat)
    host = _host(case, seat)
    machine, host_id = host.machine_id, host.id
    _idle_for(case, seat, timedelta(seconds=settings.cloud_vm_idle_release_s + 60))

    async def walked_away():
        async with case.client.test_request_factory() as db:
            await db.execute(
                update(SandboxHome)
                .where(SandboxHome.session_id == seat.session)
                .values(session_id=None)
            )
            await db.commit()

    case.client.portal.call(walked_away)

    assert _release_idle(case) == 1

    # No session is left to push: the home and its machine simply go.
    assert not _pushed_on(case, device_id)
    assert _homes_on(case, host_id) == 0
    _sweep(case)
    assert case.provider.deleted == [machine]


def test_a_vm_a_dead_turn_left_open_does_not_keep(cloud):
    """A process killed inside a turn leaves its row open and nothing closes
    it: the VM must not be held by it for good."""
    case = cloud
    seat = _room(case, WHOLE_VM)
    device_id, _ = _working(case, seat)
    machine = _host(case, seat).machine_id
    _idle_for(case, seat, timedelta(seconds=settings.cloud_vm_idle_release_s + 60))

    async def corpse():
        async with case.client.test_request_factory() as db:
            db.add(
                AgentTurn(
                    id=uuid.uuid4(),
                    conversation_id=seat.room,
                    continuation_id=uuid.uuid4(),
                    author="alice",
                    started_at=datetime.now(UTC) - timedelta(hours=3),
                )
            )
            await db.commit()

    case.client.portal.call(corpse)

    assert _release_idle(case) == 1

    assert _host(case, seat) is None
    _sweep(case)
    assert case.provider.deleted == [machine]


def test_a_vm_is_not_kept_by_another_seats_turn_in_its_room(cloud):
    """Another line working in the same room is not this session's work: its
    own VM goes, and the other line's turn holds nothing of this one's."""
    case = cloud
    seat = _room(case, WHOLE_VM)
    _working(case, seat)
    _idle_for(case, seat, timedelta(seconds=settings.cloud_vm_idle_release_s + 60))

    async def someone_else():
        async with case.client.test_request_factory() as db:
            db.add(
                AgentTurn(
                    id=uuid.uuid4(),
                    conversation_id=seat.room,
                    continuation_id=uuid.uuid4(),
                    author="alice",
                    agent_handle="another-line",
                    started_at=datetime.now(UTC) - timedelta(minutes=1),
                )
            )
            await db.commit()

    case.client.portal.call(someone_else)

    assert _release_idle(case) == 1

    assert _host(case, seat) is None


def test_an_idle_vm_whose_checkpoint_fails_is_released_all_the_same(cloud):
    case = cloud
    seat = _room(case, WHOLE_VM)
    device_id, _ = _working(case, seat)
    machine = _host(case, seat).machine_id
    _idle_for(case, seat, timedelta(seconds=settings.cloud_vm_idle_release_s + 60))
    case.calls.side_effect = lambda lease, method, *a, **k: (
        running() if method == "ping" else {"error": "remote rejected the push"}
    )

    assert _release_idle(case) == 1

    assert _pushed_on(case, device_id)
    assert _session_lease(case, seat) is None
    assert _host(case, seat) is None
    _sweep(case)
    assert case.provider.deleted == [machine]


# --- what it costs -----------------------------------------------------------


def _meter(case):
    return case.client.portal.call(
        ComputeMeterSweeper(case.client.test_request_factory).sweep
    )


def _vm_runs(case) -> list[ComputeRun]:
    async def read():
        async with case.client.test_request_factory() as db:
            return list(
                await db.scalars(
                    select(ComputeRun).where(
                        ComputeRun.project_id == case.project_id,
                        ComputeRun.kind == "vm",
                    )
                )
            )

    return case.client.portal.call(read)


def _vm_charges(case) -> list[ResourceUsage]:
    async def read():
        async with case.client.test_request_factory() as db:
            return list(
                await db.scalars(
                    select(ResourceUsage).where(
                        ResourceUsage.project_id == case.project_id,
                        ResourceUsage.route == "compute",
                    )
                )
            )

    return case.client.portal.call(read)


def test_a_whole_vm_is_charged_its_sizes_price_from_creation_to_release(cloud):
    case = cloud
    seat = _room(case, WHOLE_VM)
    _working(case, seat)
    host = _host(case, seat)
    _meter(case)
    [run] = _vm_runs(case)
    assert (run.spec, run.started_at, run.topic_id) == (
        "4c8g",
        host.created_at,
        seat.room,
    )

    # An hour after it was created, the hour is charged at the size's price
    # (30 an hour in the suite), and nothing at a sandbox's.
    async def an_hour_ago():
        async with case.client.test_request_factory() as db:
            then = datetime.now(UTC) - timedelta(hours=1)
            await db.execute(
                update(ComputeRun)
                .where(ComputeRun.id == run.id)
                .values(started_at=then, billed_until=then)
            )
            await db.commit()

    case.client.portal.call(an_hour_ago)
    _meter(case)
    [charge] = _vm_charges(case)
    assert (charge.kind, charge.credits) == ("vm", pytest.approx(30))

    # Released once idle; its run ends where the VM did.
    _idle_for(case, seat, timedelta(seconds=settings.cloud_vm_idle_release_s + 60))
    assert _release_idle(case) == 1
    _sweep(case)
    _meter(case)
    [ended] = _vm_runs(case)
    assert ended.ended_at is not None
    assert sum(c.credits for c in _vm_charges(case)) == pytest.approx(30 + 30 / 60)


def test_no_whole_vm_is_created_for_a_size_with_no_price(cloud, monkeypatch):
    case = cloud
    monkeypatch.setattr(settings, "cloud_vm_credits_per_hour", {})
    seat = _room(case, WHOLE_VM)

    answer = _lease(case, seat, timeout=0.001).json()["data"]

    assert answer["unavailable"] == (
        "虚拟机沙箱暂时无法启动：平台还没有设定这种规格的价格。对话和平台工具仍可用。"
    )
    assert case.provider.created == []
    assert _host(case, seat) is None


def test_every_sandbox_is_marked_as_its_sessions_own(cloud):
    """A 支线 or a task not yet started runs freely only where nothing else
    works (the lease's ``own``): a sandbox of its own, a VM sandbox included."""
    case = cloud
    boxed = _room(case, SANDBOX, "Sandbox")
    _working(case, boxed)
    on_vm = _room(case, WHOLE_VM, "VM")
    _working(case, on_vm)

    async def lease(seat):
        async with case.client.test_request_factory() as db:
            return (await AgentSessionService(db).by_id(seat.session)).work_lease

    assert case.client.portal.call(lease, boxed)["own"] is True
    assert case.client.portal.call(lease, on_vm)["own"] is True


def test_a_session_refused_a_machine_is_counted_once_however_often_it_asks(
    cloud, monkeypatch
):
    case = cloud
    monkeypatch.setattr(settings, "cloud_pool_max_hosts", 1)
    monkeypatch.setattr(settings, "platform_admin_handles", ["ops"])
    first, second = _room(case, WHOLE_VM, "A"), _room(case, WHOLE_VM, "B")
    assert _lease(case, first, timeout=0.001).json()["data"]["preparing"]

    for _ in range(3):
        assert "unavailable" in _lease(case, second, timeout=5).json()["data"]

    history = case.client.get(
        "/admin/stats/machines", headers=session_auth_headers("ops")
    ).json()["data"]["history"]
    assert history["refused_sessions"] == 1
    assert history["refused_by_reason"] == {"pool_full": 1}
