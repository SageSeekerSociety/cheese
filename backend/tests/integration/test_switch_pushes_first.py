"""Switching a session's work computer pushes its work first (#1900 step 4).

The push runs on the machine being left; the switch happens only after it
succeeds. When that machine cannot be reached, a person may switch anyway and
nobody else may. A Cloud machine left after a push stops counting against the
team's quota.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.common.auth import create_access_token
from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply, Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.machine import session_work as work_lease
from app.domain.machine.models import MachineStatus
from app.domain.machine.repositories import ProjectMachineRepository
from app.domain.machine.services import MachineService
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.integration.conftest import post_project
from tests.unit.test_machine_service import FakeMicroCloud

pytestmark = pytest.mark.anyio

PUSHED = {"value": {"stdout": "", "stderr": "", "interrupted": False}}


async def _room(client, *, on_cloud=False):
    """A room whose one agent session works on a ready machine, and a second
    self-hosted device the project may switch it to."""
    project = post_project(
        client, json={"name": "Switch pushes", "owner_handle": "alice"}
    ).json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room", "created_by": "alice"},
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
                visibility=Visibility.host,
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


def _machines(monkeypatch, *, online=True, push=PUSHED):
    hub = SimpleNamespace(is_online=lambda device: online)
    monkeypatch.setattr(work_lease, "device_hub", hub)
    remote = AsyncMock(
        side_effect=push if isinstance(push, BaseException) else lambda *a, **k: push
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
async def test_a_cloud_machine_left_after_a_push_stops_counting_against_quota(
    client, monkeypatch, reachable
):
    room = await _room(client, on_cloud=True)
    cloud = FakeMicroCloud()
    cloud.machines[71] = {"id": 71, "status": "running"}
    monkeypatch.setattr("app.domain.machine.services.MicroCloudClient", lambda: cloud)
    async with client.test_factory() as db:
        machine = await ProjectMachineRepository(db).add(
            project_id=room.project_id,
            topic_id=room.topic_id,
            session_id=room.session_id,
            machine_id=71,
            customer_id=7,
            account_id=9,
            offering_id=1,
            hostname="left-cloud",
            login_user="cheese",
            cores=2,
            memory_mb=4096,
            disk_gb=20,
            status=MachineStatus.running,
            ip=None,
            requested_by="alice",
        )
        machine.device_id = room.old_device
        machine_id = machine.id
        await db.commit()
    _machines(monkeypatch, online=reachable)

    switched = client.put(
        room.path,
        headers=room.person,
        json=_to_new(room, abandon_unpushed=not reachable),
    )

    assert switched.status_code == 200, switched.text
    async with client.test_factory() as db:
        service = MachineService(db, cloud)
        left = await ProjectMachineRepository(db).get(machine_id)
        counted = await service.quota_machines(
            await service.quota_team_id(room.project_id)
        )
        session = await AgentSessionService(db).by_id(room.session_id)
    assert left.superseded_at is not None
    if reachable:
        assert cloud.deleted == [71]
        assert left.released_at is not None
        assert left.status == MachineStatus.deleting
        assert machine_id not in {m.id for m in counted}
        # Nothing is left on it for the room's cleanup to look for.
        assert session.execution_request["retained_leases"] == []
    else:
        # Switched without a push: whatever was only there stays, and so does
        # the machine, until the room's cleanup.
        assert cloud.deleted == []
        assert left.released_at is None
        assert machine_id in {m.id for m in counted}
        assert session.execution_request["retained_leases"] == [room.lease]
