"""Switching a session's work computer pushes its work first (#1900 step 4).

The push runs on the machine being left; the switch happens only after it
succeeds. When that machine cannot be reached, a person may switch anyway and
nobody else may. A cloud sandbox left after a push gives its home on the host
back to the pool; one left without a push keeps it, and the host with it.
"""

import ast
import json
import re
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from app.common.auth import create_access_token
from app.core import sandbox_auth
from app.core.sandbox_auth import (
    bind_resource_token,
    mint_scoped_token,
    scoped_token_claims,
)
from app.domain.agent import execution
from app.domain.agent.device_hub import DeviceCallError
from app.domain.agent.harness.claude_code.remote_execution import launch
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.models import Block, BlockKind
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.machine import session_work as work_lease
from app.domain.machine.models import CloudHost, CloudHostHome, MachineStatus
from app.domain.topic.models import Topic
from app.domain.user.models import User
from app.main import app
from tests.delivery import delivery_task
from tests.executor_release import running
from tests.integration.conftest import post_project, session_auth_headers

pytestmark = pytest.mark.anyio

PUSHED = {"value": {"stdout": "", "stderr": "", "interrupted": False}}


async def _room(client, *, on_cloud=False):
    """A room whose one agent session works on a ready machine, and a second
    self-hosted device the project may switch it to."""
    project = post_project(
        client, json={"name": "Switch pushes"}, owner="alice"
    ).json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(room["id"])
    async with client.test_factory() as db:
        owner = await db.scalar(select(User).where(User.username == "alice"))
        devices = sql_device_service(db)
        ids = []
        for name in ("old", "new"):
            device = await devices.approve(
                await devices.start(name),
                owner_user_id=owner.id,
                supply=Supply.self_hosted,
            )
            await devices.assign_to_project(
                device.device_id, project_id, actor_user_id=owner.id
            )
            ids.append(device.device_id)
        old_device, new_device = ids
        agent = await IdentityService(db).ensure_room_agent_user(topic_id)
        topic = await db.get(Topic, topic_id)
        resource = str(topic.resource_id or topic_id)
        session = await AgentSessionService(db).ensure(
            topic_id, agent.username, harness="claude-code"
        )
        generation = str(uuid.uuid4())
        if on_cloud:
            choice = {"name": "Cloud", "profile": "cloud"}
        else:
            choice = {"name": "Old", "profile": "device", "device_id": old_device}
        session.execution_request = {
            "generation": generation,
            "choice": choice,
            "authorized_by": None,
        }
        session.work_lease = {
            "kind": "device",
            "session_id": str(session.id),
            "generation": generation,
            "resource_id": str(uuid.uuid4()),
            "room_resource_id": resource,
            "device_id": old_device,
            "status": "ready",
            "home": "/old",
            "state": "/old/state",
            "workspace": "/old/work",
            "mcp_servers": [],
        }
        session.runtime_location = {
            "device_id": "center",
            "resource_id": resource,
            "channel": "device",
        }
        topic.compute_config = choice
        agent_token = bind_resource_token(
            mint_scoped_token(
                project_id=str(project_id),
                topic_id=str(topic_id),
                agent_handle=agent.username,
            ),
            resource,
            session_id=str(session.id),
        )
        person = {
            "Authorization": "Bearer "
            + create_access_token(owner.id, handle=owner.username)
        }
        await db.commit()
        return SimpleNamespace(
            project_id=project_id,
            topic_id=topic_id,
            session_id=session.id,
            old_device=old_device,
            new_device=new_device,
            lease=dict(session.work_lease),
            person=person,
            agent={"X-Cheese-Token": agent_token},
            lease_path=f"/topics/{topic_id}/sessions/{session.id}/work-lease",
            path=f"/topics/{topic_id}/compute-profile",
        )


def _machines(monkeypatch, *, online=True, reconnecting=False, push=PUSHED):
    hub = SimpleNamespace(
        is_online=lambda device: online,
        reconnecting=lambda device: reconnecting,
    )
    monkeypatch.setattr(work_lease, "device_hub", hub)
    remote = AsyncMock(
        side_effect=push
        if isinstance(push, BaseException)
        else lambda lease, method, *a, **k: running() if method == "ping" else push
    )
    monkeypatch.setattr(execution, "call", remote)
    return remote


def _to_new(room, **extra):
    return {
        "choice": {"name": "New", "profile": "device", "device_id": room.new_device},
        **extra,
    }


async def _session(client, room):
    async with client.test_factory() as db:
        return await AgentSessionService(db).by_id(room.session_id)


async def test_a_switch_pushes_on_the_old_machine_before_it_happens(
    client, monkeypatch
):
    room = await _room(client)
    remote = _machines(monkeypatch)

    switched = client.put(room.path, headers=room.person, json=_to_new(room))

    assert switched.status_code == 200, switched.text
    target, method, params = remote.await_args.args
    assert target["device_id"] == room.old_device
    assert method == "control" and params["subtype"] == "checkpoint"
    session = await _session(client, room)
    assert session.execution_request["choice"]["device_id"] == room.new_device
    assert session.work_lease is None
    # A self-hosted machine keeps the session's directory for the room's cleanup.
    assert session.execution_request["retained_leases"] == [room.lease]


async def test_an_old_machine_that_just_dropped_is_still_pushed_on(client, monkeypatch):
    """Its link dropped a moment ago and it is on its way back: the push goes
    to it and waits there, rather than the switch being refused as if the
    machine were gone."""
    room = await _room(client)
    remote = _machines(monkeypatch, online=False, reconnecting=True)

    switched = client.put(room.path, headers=room.person, json=_to_new(room))

    assert switched.status_code == 200, switched.text
    target, method, _params = remote.await_args.args
    assert target["device_id"] == room.old_device and method == "control"


@pytest.mark.parametrize(
    "push",
    [
        {
            "value": {
                "stdout": "Exit code 1\n"
                "[cheese] 任务 t1 同步失败：rejected non-fast-forward"
            }
        },
        {"error": "Stop hook failed"},
    ],
)
async def test_a_failed_push_refuses_the_switch_and_says_why(client, monkeypatch, push):
    room = await _room(client)
    _machines(monkeypatch, push=push)

    refused = client.put(room.path, headers=room.person, json=_to_new(room))

    assert refused.status_code == 409, refused.text
    said = refused.json()["error"]["message"]
    assert said.startswith("推送失败，没有更换")
    assert "non-fast-forward" in said or "Stop hook failed" in said
    # Nobody can switch past a push that ran and failed.
    forced = client.put(
        room.path, headers=room.person, json=_to_new(room, abandon_unpushed=True)
    )
    assert forced.status_code == 409, forced.text
    session = await _session(client, room)
    assert session.work_lease == room.lease
    assert session.execution_request["choice"]["device_id"] == room.old_device


async def test_a_failed_push_names_every_task_it_could_not_sync_and_why(
    client, monkeypatch
):
    room = await _room(client)
    refused_by_api = (
        "[cheese] GET /projects/p/git/tasks/{task} 失败 HTTP 401: "
        + '{"code":401,"message":"AuthenticationRequiredError: '
        + "git access needs this project's token\"}"
        + " " * 300
    )
    printed = "".join(
        f"{refused_by_api.replace('{task}', task)}\n"
        f"[cheese] 同步失败的通知未送达，任务 {task} 的 cheese-sync.log 保留了结果\n"
        f"[cheese] 任务 {task} 同步失败：1\n"
        for task in ("task-a", "task-b")
    )
    printed += "[cheese] 任务 task-c 同步失败：rejected non-fast-forward\n"
    _machines(monkeypatch, push={"value": {"stdout": "Exit code 1\n" + printed}})

    refused = client.put(room.path, headers=room.person, json=_to_new(room))

    assert refused.status_code == 409, refused.text
    said = refused.json()["error"]["message"]
    lines = said.splitlines()
    assert lines[0].startswith("推送失败，没有更换")
    for task in ("task-a", "task-b"):
        [line] = [line for line in lines if f"任务 {task}" in line]
        assert "HTTP 401" in line and "this project's token" in line
    [line] = [line for line in lines if "任务 task-c" in line]
    assert "rejected non-fast-forward" in line


@pytest.mark.parametrize("how", ["offline", "no answer"])
async def test_an_unreachable_old_machine_only_a_person_can_switch_past(
    client, monkeypatch, how
):
    room = await _room(client)
    if how == "offline":
        remote = _machines(monkeypatch, online=False)
    else:
        remote = _machines(monkeypatch, push=TimeoutError("no answer"))

    person = client.put(room.path, headers=room.person, json=_to_new(room))
    assert person.status_code == 409, person.text
    assert person.json()["error"]["name"] == "WorkComputerUnreachable"
    assert "连不上" in person.json()["error"]["message"]
    # The agent's own switch (`cheese_machine`) goes through the room route and
    # cannot skip the push, whatever it sends.
    agent = client.put(
        f"/topics/{room.topic_id}/compute-profile",
        headers=room.agent,
        json={
            "profile": "device",
            "device_id": room.new_device,
            "abandon_unpushed": True,
        },
    )
    assert agent.status_code == 409, agent.text
    assert agent.json()["error"]["name"] == "WorkComputerUnreachable"
    session = await _session(client, room)
    assert session.execution_request["choice"]["device_id"] == room.old_device

    forced = client.put(
        room.path, headers=room.person, json=_to_new(room, abandon_unpushed=True)
    )

    assert forced.status_code == 200, forced.text
    session = await _session(client, room)
    assert session.execution_request["choice"]["device_id"] == room.new_device
    assert session.execution_request["retained_leases"] == [room.lease]
    if how == "offline":
        remote.assert_not_awaited()


@pytest.mark.parametrize("reachable", [True, False])
async def test_a_cloud_sandbox_left_after_a_push_gives_its_home_back(
    client, monkeypatch, reachable
):
    room = await _room(client, on_cloud=True)
    async with client.test_factory() as db:
        host = CloudHost(
            machine_id=71,
            customer_id=7,
            account_id=9,
            offering_id=1,
            hostname="pool-host",
            login_user="cheese",
            cores=2,
            memory_mb=4096,
            disk_gb=20,
            status=MachineStatus.running,
            device_id=room.old_device,
        )
        db.add(host)
        await db.flush()
        db.add(
            CloudHostHome(
                host_id=host.id,
                project_id=room.project_id,
                topic_id=room.topic_id,
                room_resource_id=room.lease["room_resource_id"],
                resource_id=room.lease["resource_id"],
                session_id=room.session_id,
            )
        )
        host_id = host.id
        await db.commit()
    _machines(monkeypatch, online=reachable)

    switched = client.put(
        room.path,
        headers=room.person,
        json=_to_new(room, abandon_unpushed=not reachable),
    )

    assert switched.status_code == 200, switched.text
    async with client.test_factory() as db:
        homes = list(
            await db.scalars(
                select(CloudHostHome).where(CloudHostHome.host_id == host_id)
            )
        )
        host = await db.get(CloudHost, host_id)
        session = await AgentSessionService(db).by_id(room.session_id)
    # The host is the pool's either way: a switch never deletes it.
    assert host.released_at is None
    if reachable:
        # Pushed: nothing of the session's is only there, so its slot is free.
        assert homes == []
        assert session.execution_request["retained_leases"] == []
    else:
        # Switched without a push: whatever was only there stays, and so does
        # the home, which keeps the host until the room's cleanup.
        assert [home.left_at is not None for home in homes] == [True]
        assert session.execution_request["retained_leases"] == [room.lease]


class _IdleMachine:
    """An online machine on which the session's executor has exited: a call
    to it finds no socket until the installation starts it again."""

    def __init__(self, push=PUSHED, install_exit=0):
        self.started = False
        self.push = push
        self.install_exit = install_exit
        self.calls = []
        self.installs = []

    def is_online(self, device):
        return True

    async def call_executor(self, device_id, state, method, params, **kwargs):
        self.calls.append((device_id, method, params))
        if not self.started:
            raise DeviceCallError(
                "dial unix /tmp/cheese-execution-1000-x.sock: connect: "
                "no such file or directory"
            )
        return running() if method == "ping" else self.push

    async def exec(self, device_id, argv, *, stdin, timeout):
        self.installs.append((device_id, stdin))
        if self.install_exit:
            return {"exit": self.install_exit, "stderr": "no python3"}
        self.started = True
        return {
            "exit": 0,
            "stdout": json.dumps(
                {"state": "/old/state", "workspace": "/old/work", "mcp_servers": []}
            ),
        }


async def test_an_idle_sessions_executor_is_started_to_push_before_a_switch(
    client, monkeypatch
):
    room = await _room(client)
    machine = _IdleMachine()
    monkeypatch.setattr(work_lease, "device_hub", machine)

    switched = client.put(room.path, headers=room.person, json=_to_new(room))

    assert switched.status_code == 200, switched.text
    # Started on the machine being left, then pushed there.
    [(device, script)] = machine.installs
    assert device == room.old_device
    device, method, params = machine.calls[-1]
    assert device == room.old_device
    assert method == "control" and params["subtype"] == "checkpoint"
    # With a credential for this session that is good now.
    [token] = re.findall(r'"CHEESE_TOKEN": "([^"]+)"', script)
    claims = scoped_token_claims(token)
    assert claims is not None
    assert claims["session"] == str(room.session_id)
    assert claims["t"] == str(room.topic_id)
    session = await _session(client, room)
    assert session.execution_request["choice"]["device_id"] == room.new_device


async def test_a_machine_that_cannot_start_the_executor_is_unreachable(
    client, monkeypatch
):
    room = await _room(client)
    monkeypatch.setattr(work_lease, "device_hub", _IdleMachine(install_exit=1))

    refused = client.put(room.path, headers=room.person, json=_to_new(room))

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["name"] == "WorkComputerUnreachable"
    session = await _session(client, room)
    assert session.execution_request["choice"]["device_id"] == room.old_device


class _PushingMachine(_IdleMachine):
    """An idle session's machine whose push does what ``cheese sync`` does for
    a task with commits not yet on its branch: ask the platform for that task's
    branch, with the credential its executor was started with."""

    def __init__(self, project_id, task_id):
        super().__init__()
        self.path = f"/projects/{project_id}/git/tasks/{task_id}"
        self.task_id = task_id
        self.finishes = False

    async def call_executor(self, device_id, state, method, params, **kwargs):
        if method != "control" or not self.started:
            return await super().call_executor(
                device_id, state, method, params, **kwargs
            )
        self.calls.append((device_id, method, params))
        [token] = re.findall(r'"CHEESE_TOKEN": "([^"]+)"', self.installs[-1][1])
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as api:
            asked = await api.get(self.path, headers={"X-Cheese-Token": token})
        if asked.status_code != 200:
            said = (
                f"Exit code 1\n[cheese] GET {self.path} 失败 HTTP "
                f"{asked.status_code}: {asked.text}\n"
                f"[cheese] 任务 {self.task_id} 同步失败：1"
            )
            return {"value": {"stdout": said}}
        if not self.finishes:
            return {"value": {"stdout": "", "backgroundTaskId": "task-slow"}}
        return PUSHED


async def test_an_executor_started_for_a_push_can_still_push_hours_later(
    client, monkeypatch
):
    """A switch starts an idle session's executor to push; that push outlasts
    its two minutes and the switch is refused, leaving the executor running.
    Hours later the switch is asked again: the executor is still up, so it is
    not started again, and its push must still be let through by the git
    routes, as the push of a session started by a turn would be."""
    room = await _room(client)
    task = delivery_task(client, room.topic_id, commit=False)
    machine = _PushingMachine(room.project_id, task.id)
    monkeypatch.setattr(work_lease, "device_hub", machine)

    first = client.put(room.path, headers=room.person, json=_to_new(room))
    assert first.status_code == 409, first.text
    assert "两分钟" in first.json()["error"]["message"]

    now = sandbox_auth.time.time
    later = SimpleNamespace(time=lambda: now() + 3 * 3600)
    monkeypatch.setattr(sandbox_auth, "time", later)
    machine.finishes = True
    again = client.put(room.path, headers=room.person, json=_to_new(room))

    assert again.status_code == 200, again.text
    assert len(machine.installs) == 1
    session = await _session(client, room)
    assert session.execution_request["choice"]["device_id"] == room.new_device


async def test_an_executor_started_to_push_is_installed_with_the_rooms_environment(
    client, monkeypatch
):
    room = await _room(client)
    machine = _IdleMachine()
    monkeypatch.setattr(work_lease, "device_hub", machine)

    switched = client.put(room.path, headers=room.person, json=_to_new(room))

    assert switched.status_code == 200, switched.text
    # The machine records the environment an executor was installed with and
    # refuses the next turn's start while it differs from the room's, so the
    # one started for the push must be installed with the room's.
    [(_, script)] = machine.installs
    [payload] = re.findall(r"configure\(json\.loads\((.+)\)\)\n$", script)
    installed = json.loads(ast.literal_eval(payload))["environment"]
    pinned = client.get(
        f"/projects/{room.project_id}/environment/rooms/{room.topic_id}",
        headers=room.person,
    ).json()["data"]["pinned_revision"]
    assert installed is not None
    assert installed["revision"] == pinned


async def test_an_executor_on_an_older_release_is_started_again_to_push(
    client, monkeypatch
):
    """An executor keeps the `cheese` it was installed with. A switch that
    pushed through one from before a sync fix failed the way that fix had
    already stopped, so it is started again on this release first."""
    room = await _room(client)
    machine = _IdleMachine()
    machine.started = True
    older = {**launch.file_sources(), "cheese": "# the CLI of an older release\n"}

    async def call_executor(device_id, state, method, params, **kwargs):
        if method == "ping":
            machine.calls.append((device_id, method, params))
            return running() if machine.installs else running(older)
        return await _IdleMachine.call_executor(
            machine, device_id, state, method, params, **kwargs
        )

    machine.call_executor = call_executor
    monkeypatch.setattr(work_lease, "device_hub", machine)

    switched = client.put(room.path, headers=room.person, json=_to_new(room))

    assert switched.status_code == 200, switched.text
    [(device, _)] = machine.installs
    assert device == room.old_device
    _, method, params = machine.calls[-1]
    assert method == "control" and params["subtype"] == "checkpoint"


def _closed_task_failed(task, reason):
    return {
        "value": {
            "stdout": f"Exit code 1\n[cheese] 已结束的任务 {task} 同步失败：{reason}"
        }
    }


async def _room_events(client, room, event_type):
    async with client.test_factory() as db:
        blocks = await db.scalars(
            select(Block).where(
                Block.topic_id == room.topic_id, Block.kind == BlockKind.event
            )
        )
        return [b for b in blocks if (b.meta or {}).get("event_type") == event_type]


async def test_a_closed_tasks_failed_backup_does_not_hold_a_room_on_its_own_machine(
    client, monkeypatch
):
    """A self-hosted machine keeps its files after the room leaves it, so a
    closed task whose leftovers could not be backed up loses nothing: the room
    moves, and the switch and the room both say which task and why."""
    room = await _room(client)
    _machines(
        monkeypatch,
        push=_closed_task_failed("t-closed", "fatal: ref HEAD is not a symbolic ref"),
    )

    switched = client.put(room.path, headers=room.person, json=_to_new(room))

    assert switched.status_code == 200, switched.text
    [warning] = switched.json()["data"]["warnings"]
    assert "t-closed" in warning and "not a symbolic ref" in warning
    session = await _session(client, room)
    assert session.execution_request["choice"]["device_id"] == room.new_device
    assert session.execution_request["retained_leases"] == [room.lease]
    [told] = await _room_events(client, room, "work_left_on_machine")
    assert "t-closed" in told.meta["detail"]
    assert "not a symbolic ref" in told.meta["detail"]


async def test_a_closed_tasks_failed_backup_still_holds_a_room_on_a_cloud_machine(
    client, monkeypatch
):
    """A Cloud machine is deleted once the room leaves it: a closed task's
    leftovers that were not backed up would go with it."""
    room = await _room(client, on_cloud=True)
    _machines(
        monkeypatch,
        push=_closed_task_failed("t-closed", "Connection reset by peer"),
    )

    refused = client.put(room.path, headers=room.person, json=_to_new(room))

    assert refused.status_code == 409, refused.text
    said = refused.json()["error"]["message"]
    assert "t-closed" in said and "Connection reset by peer" in said
    session = await _session(client, room)
    assert session.work_lease == room.lease
    assert not await _room_events(client, room, "work_left_on_machine")


async def test_an_open_tasks_failed_push_holds_the_room_beside_a_closed_one(
    client, monkeypatch
):
    room = await _room(client)
    printed = (
        "[cheese] 已结束的任务 t-closed 同步失败："
        "fatal: ref HEAD is not a symbolic ref\n"
        "[cheese] 任务 t-open 同步失败：rejected non-fast-forward\n"
    )
    _machines(monkeypatch, push={"value": {"stdout": "Exit code 1\n" + printed}})

    refused = client.put(room.path, headers=room.person, json=_to_new(room))

    assert refused.status_code == 409, refused.text
    said = refused.json()["error"]["message"]
    assert "t-open" in said and "non-fast-forward" in said
    session = await _session(client, room)
    assert session.execution_request["choice"]["device_id"] == room.old_device
