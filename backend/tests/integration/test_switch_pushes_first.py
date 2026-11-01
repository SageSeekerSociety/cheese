"""Switching a session's execution environment checkpoints its work first, once.

The checkpoint (`cheese sync --all`) runs on the machine being left, and the
switch happens whether or not it went through: a machine that is away, a push
that fails or outlasts its wait, an executor that cannot start — none of them
holds the room, for a person or for its agent. The platform keeps only what
was pushed, so work a lost checkpoint did not push stays on the machine being
left and is not carried anywhere. A cloud sandbox left behind is left to the
idle sweep either way.
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
from app.core.config import settings
from app.core.sandbox_auth import (
    bind_resource_token,
    mint_scoped_token,
    scoped_token_claims,
)
from app.domain.agent import execution
from app.domain.agent.device_hub import DeviceCallError
from app.domain.agent.harness.claude_code.remote_execution import launch
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.adapters import platform_line
from app.domain.delivery.ledger import settle_card
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.machine import session_work as work_lease
from app.domain.machine.models import CloudHost, MachineStatus, SandboxHome
from app.domain.machine.session_work import checkpoint_room
from app.domain.review.archive import close_cards_for_archived_topic
from app.domain.room_cleanup import retire
from app.domain.room_task.archive import close_room_tasks
from app.domain.topic.models import Topic
from app.domain.topic.services import TopicService
from app.domain.user.models import User
from app.main import app
from tests.delivery import delivery_task
from tests.executor_release import running
from tests.integration.conftest import post_project, session_auth_headers
from tests.support.devices import enrollment_team, place_in_project_team

pytestmark = pytest.mark.anyio

PUSHED = {"value": {"stdout": "", "stderr": "", "interrupted": False}}


async def _room(client, *, on_cloud=False, in_task=False):
    """A room whose one agent session works on a ready machine, and a second
    self-hosted device the project may switch it to. ``in_task``: the session
    is the one working a task of the room, in the task's own conversation."""
    project = post_project(
        client, json={"name": "Switch pushes"}, owner="alice"
    ).json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(room["id"])
    conversation_id = topic_id
    if in_task:
        conversation_id = uuid.UUID(
            client.post(
                f"/topics/{topic_id}/tasks",
                json={"title": "Task"},
                headers=session_auth_headers("alice"),
            ).json()["data"]["id"]
        )
    async with client.test_factory() as db:
        owner = await db.scalar(select(User).where(User.username == "alice"))
        devices = sql_device_service(db)
        ids = []
        for name in ("old", "new"):
            device = await devices.approve(
                await devices.start(name),
                owner_user_id=owner.id,
                supply=Supply.self_hosted,
                team_id=await enrollment_team(devices, owner.id),
            )
            await place_in_project_team(
                devices, device.device_id, project_id, actor_user_id=owner.id
            )
            ids.append(device.device_id)
        old_device, new_device = ids
        agent = await IdentityService(db).ensure_room_agent_user(topic_id)
        topic = await db.get(Topic, topic_id)
        resource = str(topic.resource_id or topic_id)
        session = await AgentSessionService(db).ensure(
            conversation_id, agent.username, harness="claude-code"
        )
        generation = str(uuid.uuid4())
        if on_cloud:
            environment = {"kind": "sandbox", "spec": "standard"}
        else:
            environment = {"kind": "self_hosted", "device_id": old_device}
        await AgentSessionService(db).set_environment(
            conversation_id, agent.username, environment, chosen_by=None
        )
        session.execution_request = {"generation": generation}
        session.work_lease = {
            "kind": "device",
            **({"platform": True} if on_cloud else {}),
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
            conversation_id=conversation_id,
            session_id=session.id,
            old_device=old_device,
            new_device=new_device,
            lease=dict(session.work_lease),
            person=person,
            agent={"Authorization": f"Bearer {agent_token}"},
            lease_path=f"/topics/{topic_id}/sessions/{session.id}/work-lease",
            agent_handle=agent.username,
            path=f"/topics/{conversation_id}/sessions/{session.id}/environment",
        )


def _machines(monkeypatch, *, online=True, reconnecting=False, push=PUSHED):
    hub = SimpleNamespace(
        target=lambda _device: "linux-amd64",
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
        "environment": {"kind": "self_hosted", "device_id": room.new_device},
        **extra,
    }


async def _session(client, room):
    async with client.test_factory() as db:
        return await AgentSessionService(db).by_id(room.session_id)


async def _owner_id(client) -> int:
    async with client.test_factory() as db:
        return (await db.scalar(select(User).where(User.username == "alice"))).id


async def _environment(client, room) -> dict:
    async with client.test_factory() as db:
        chosen = await AgentSessionService(db).environment(
            room.conversation_id, room.agent_handle
        )
        return chosen.environment


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
    assert (await _environment(client, room))["device_id"] == room.new_device
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


FAILED_PUSHES = {
    "a task did not sync": {
        "value": {
            "stdout": "Exit code 1\n"
            "[cheese] 任务 t1 同步失败：rejected non-fast-forward"
        }
    },
    "the command failed": {"error": "Stop hook failed"},
    "it outlasted its wait": {"value": {"stdout": "", "backgroundTaskId": "task-slow"}},
    "no answer": TimeoutError("no answer"),
}


@pytest.mark.parametrize("push", FAILED_PUSHES.values(), ids=FAILED_PUSHES.keys())
async def test_a_failed_checkpoint_does_not_hold_the_switch(client, monkeypatch, push):
    room = await _room(client)
    remote = _machines(monkeypatch, push=push)

    switched = client.put(room.path, headers=room.person, json=_to_new(room))

    assert switched.status_code == 200, switched.text
    remote.assert_awaited()
    session = await _session(client, room)
    assert (await _environment(client, room))["device_id"] == room.new_device
    assert session.work_lease is None
    assert session.execution_request["retained_leases"] == [room.lease]


async def test_an_agent_switches_away_from_an_unreachable_machine_by_itself(
    client, monkeypatch
):
    """Nothing is refused over work that could not be pushed, so the agent
    switches on its own (`cheese_session(action=environment)`) and never has to find a person
    to do it."""
    room = await _room(client)
    remote = _machines(monkeypatch, online=False)
    sandbox = {"kind": "sandbox", "spec": "team_devices"}

    switched = client.put(room.path, headers=room.agent, json={"environment": sandbox})

    assert switched.status_code == 200, switched.text
    remote.assert_not_awaited()
    assert await _environment(client, room) == sandbox


@pytest.mark.parametrize("reachable", [True, False])
async def test_a_cloud_sandbox_left_is_left_to_the_idle_sweep_either_way(
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
            SandboxHome(
                host_id=host.id,
                spec="standard",
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

    switched = client.put(room.path, headers=room.person, json=_to_new(room))

    assert switched.status_code == 200, switched.text
    async with client.test_factory() as db:
        homes = list(
            await db.scalars(select(SandboxHome).where(SandboxHome.host_id == host_id))
        )
        host = await db.get(CloudHost, host_id)
        session = await AgentSessionService(db).by_id(room.session_id)
    # The host is the pool's: a switch never deletes it. The sandbox left there
    # is no session's, and the idle sweep destroys it (``lifecycle``).
    assert host.released_at is None
    assert [home.session_id for home in homes] == [None]
    assert session.execution_request["retained_leases"] == []


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

    async def exec(self, device_id, argv, *, stdin, timeout, **_):
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
    assert (await _environment(client, room))["device_id"] == room.new_device


async def test_a_tasks_executor_started_to_push_is_told_it_works_the_task(
    client, monkeypatch
):
    """The session working a task is told the task as its conversation at its
    install; started again only to push before a switch, it is the same
    session and is told the same, so what it reports lands in the task."""
    room = await _room(client, in_task=True)
    machine = _IdleMachine()
    monkeypatch.setattr(work_lease, "device_hub", machine)

    switched = client.put(room.path, headers=room.person, json=_to_new(room))

    assert switched.status_code == 200, switched.text
    [(_, script)] = machine.installs
    [told] = re.findall(r'"CHEESE_TOPIC": "([^"]+)"', script)
    assert told == str(room.conversation_id)


async def test_a_machine_that_cannot_start_the_executor_does_not_hold_the_switch(
    client, monkeypatch
):
    room = await _room(client)
    monkeypatch.setattr(work_lease, "device_hub", _IdleMachine(install_exit=1))

    switched = client.put(room.path, headers=room.person, json=_to_new(room))

    assert switched.status_code == 200, switched.text
    assert (await _environment(client, room))["device_id"] == room.new_device


class _PushingMachine(_IdleMachine):
    """An idle session's machine whose push does what ``cheese sync`` does for
    a task with commits not yet on its branch: ask the platform on its
    platform socket (`/machine/api/...`) for that task's branch."""

    def __init__(self, project_id, task_id):
        super().__init__()
        self.path = f"/machine/api/projects/{project_id}/git/tasks/{task_id}"
        self.task_id = task_id
        self.finishes = False
        self.answered = None

    async def call_executor(self, device_id, state, method, params, **kwargs):
        if method != "control" or not self.started:
            return await super().call_executor(
                device_id, state, method, params, **kwargs
            )
        self.calls.append((device_id, method, params))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as api:
            asked = await api.get(
                self.path,
                headers={
                    "x-device-connection-secret": settings.device_connection_secret,
                    "x-machine-device": device_id,
                    "x-machine-state": state,
                },
            )
        self.answered = asked.status_code
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


async def test_an_executor_started_for_a_checkpoint_pushes_with_its_own_credential(
    client, monkeypatch
):
    """A switch starts an idle session's executor to checkpoint; the git routes
    let its push through as they would a push of a session started by a turn,
    and the switch does not wait for more than one."""
    monkeypatch.setattr(settings, "device_connection_secret", "owner-secret")
    room = await _room(client)
    task = delivery_task(client, room.topic_id, commit=False)
    machine = _PushingMachine(room.project_id, task.id)
    machine.finishes = True
    monkeypatch.setattr(work_lease, "device_hub", machine)

    switched = client.put(room.path, headers=room.person, json=_to_new(room))

    assert switched.status_code == 200, switched.text
    assert len(machine.installs) == 1
    [(_, method, _)] = [call for call in machine.calls if call[1] == "control"]
    assert method == "control"
    assert machine.answered == 200
    assert (await _environment(client, room))["device_id"] == room.new_device


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


ARCHIVE_CHECKPOINTS = {
    "it went through": PUSHED,
    "it failed": {"error": "remote rejected the push"},
    "no answer": TimeoutError("no answer"),
}


@pytest.mark.parametrize(
    "push", ARCHIVE_CHECKPOINTS.values(), ids=ARCHIVE_CHECKPOINTS.keys()
)
async def test_an_archived_rooms_cleanup_checkpoints_once_and_goes_on(
    client, monkeypatch, push
):
    """Before the room's sessions are stopped, each gets the same one
    checkpoint a switch gives; the cleanup removes the room whatever it
    answered, and does not ask again."""
    room = await _room(client)
    remote = _machines(monkeypatch, push=push)
    monkeypatch.setattr(settings, "topic_archive_cleanup_delay_s", 0)
    async with client.test_factory() as db:
        await TopicService(db).archive(
            room.topic_id,
            by="alice",
            settle_card=settle_card,
            cards=close_cards_for_archived_topic,
            close_tasks=close_room_tasks,
            events=platform_line,
        )
        await db.commit()
    entry = {
        "kind": "device",
        "device_id": room.old_device,
        "resource_id": room.lease["resource_id"],
    }
    monkeypatch.setattr(retire, "_inventory", AsyncMock(return_value=[entry]))
    checkpointed_first = []

    async def action(device_id, project_id, resource_id, name, *rest):
        if name == "prepare":
            # The machine was asked before the room's sessions were stopped.
            checkpointed_first.append(remote.await_count > 0)
        return {}

    monkeypatch.setattr(retire, "_device_action", AsyncMock(side_effect=action))

    swept = client.portal.call(
        lambda: retire.sweep_retired_storage(
            client.test_request_factory, checkpoint=checkpoint_room
        )
    )

    assert swept == {"completed": 1, "pending": 0}
    assert checkpointed_first and all(checkpointed_first)
    controls = [c for c in remote.await_args_list if c.args[1] == "control"]
    assert [c.args[2]["subtype"] for c in controls] in (["checkpoint"], [])
