"""A Cloud room rents one machine, and every agent in it works there.

一个话题一个容器（2026-09-28 决定，推翻结论 60）: each agent session in a room
gets its own directory on the room's machine, never a machine of its own. On a
Cloud room each new agent used to rent another VM, so a room with four agents
held four machines against the team's quota.

The machine goes when the room leaves it, and only after every session on it
has pushed: deleting it once the first session had moved destroyed what the
others had not pushed yet.
"""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.common.auth import create_access_token
from app.core.config import settings
from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent.compute_configs import standard_choice
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.machine import session_work as work_lease
from app.domain.machine.models import MachineStatus, ProjectMachine
from app.domain.machine.repositories import ProjectMachineRepository
from app.domain.machine.services import MachineService
from app.domain.project.models import Project
from app.domain.team.models import Team
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.executor_release import running
from tests.integration.conftest import post_project, session_auth_headers
from tests.unit.test_machine_service import FakeMicroCloud

HANDLES = ("ada", "zed")
PUSHED = {"value": {"stdout": "", "stderr": "", "interrupted": False}}


@pytest.fixture
def cloud_room(client, monkeypatch):
    """One Cloud room with two agents in it, neither of which has run yet."""
    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    cloud = FakeMicroCloud()
    monkeypatch.setattr(
        work_lease, "MachineService", lambda db: MachineService(db, cloud)
    )
    monkeypatch.setattr("app.domain.machine.services.MicroCloudClient", lambda: cloud)
    project = post_project(
        client, json={"name": "Shared cloud room"}, owner="alice"
    ).json()["data"]
    project_id = uuid.UUID(project["id"])
    room_id = uuid.UUID(
        client.post(
            "/topics",
            json={"project_id": project["id"], "title": "Room"},
            headers=session_auth_headers("alice"),
        ).json()["data"]["id"]
    )

    async def seed():
        async with client.test_request_factory() as db:
            team = Team(
                name="Compute team",
                handle=f"t-{uuid.uuid4().hex[:12]}",
                intro="",
                description="",
                avatar_id=1,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            db.add(team)
            await db.flush()
            (await db.get(Project, project_id)).team_id = team.id
            topic = await db.get(Topic, room_id)
            topic.compute_config = standard_choice("cloud").model_dump()
            resource = str(topic.resource_id or room_id)
            # Every session's tools in the room carry the room's agent.
            agent = await IdentityService(db).ensure_room_agent_user(room_id)
            seats = []
            for handle in HANDLES:
                db.add(
                    AgentInstance(
                        project_id=project_id, handle=handle, configuration={}
                    )
                )
                await db.flush()
                row = await AgentSessionService(db).ensure(
                    room_id, handle, harness="claude-code"
                )
                row.runtime_location = {
                    "device_id": "center",
                    "resource_id": resource,
                    "channel": "cloud",
                }
                token = bind_resource_token(
                    mint_scoped_token(
                        project_id=str(project_id),
                        topic_id=str(room_id),
                        agent_handle=agent.username,
                    ),
                    resource,
                    session_id=str(row.id),
                )
                seats.append((row.id, token))
            owner = await db.scalar(select(User).where(User.username == "alice"))
            await db.commit()
            return seats, owner.id

    seats, owner_id = client.portal.call(seed)
    online = set()

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
        target=lambda _device: "linux-amd64",
        is_online=lambda device: device in online,
        reconnecting=lambda device: False,
        exec=AsyncMock(side_effect=install),
    )
    monkeypatch.setattr(work_lease, "device_hub", hub)
    monkeypatch.setattr(execution, "call", AsyncMock(return_value={}))
    monkeypatch.setattr(work_lease, "PREPARING_WAIT_S", 1.0)
    return SimpleNamespace(
        client=client,
        cloud=cloud,
        online=online,
        project_id=project_id,
        room_id=room_id,
        owner_id=owner_id,
        seats=seats,
    )


def _lease(case, seat):
    session_id, token = seat
    return case.client.post(
        f"/topics/{case.room_id}/sessions/{session_id}/work-lease",
        headers={"X-Cheese-Token": token},
        json={"env": {}, "timeout": 5},
    )


def _rooms_machines(case):
    async def read():
        async with case.client.test_request_factory() as db:
            return list(
                await db.scalars(
                    select(ProjectMachine).where(
                        ProjectMachine.topic_id == case.room_id,
                        ProjectMachine.released_at.is_(None),
                    )
                )
            )

    return case.client.portal.call(read)


def _machine_is_up(case, machine, device_id):
    """What the enrolment sweep does once the connector on it dials in."""

    async def enrol():
        async with case.client.test_request_factory() as db:
            row = await db.get(ProjectMachine, machine.id)
            case.cloud.machines[row.machine_id].update(
                status="running", aiStatus="ready"
            )
            await ProjectMachineRepository(db).mark_enrolled(
                row, device_id=device_id, when=datetime.now(UTC)
            )
            await db.commit()

    case.client.portal.call(enrol)
    case.online.add(device_id)


def test_every_agent_in_a_cloud_room_works_on_the_rooms_one_machine(cloud_room):
    case = cloud_room
    first, second = case.seats
    opened = _lease(case, first)
    assert opened.json()["data"].get("preparing"), opened.text
    [machine] = _rooms_machines(case)
    _machine_is_up(case, machine, "rooms-machine")
    ada = _lease(case, first).json()["data"]["target"]

    # The second agent asks for its hands: the room's machine, not a new one.
    answer = _lease(case, second)
    assert answer.status_code == 200, answer.text
    zed = answer.json()["data"].get("target")
    assert zed is not None, answer.text
    assert zed["device_id"] == ada["device_id"] == "rooms-machine"
    assert len(case.cloud.created) == 1
    assert [m.id for m in _rooms_machines(case)] == [machine.id]
    # Its own directory there.
    assert zed["resource_id"] != ada["resource_id"]


def test_each_session_on_a_cloud_machine_has_its_executor_sandboxed(cloud_room):
    """A Cloud machine's sessions are `isolated`: each one's executor is
    installed to run in a sandbox of its own (#2320)."""
    import ast

    case = cloud_room
    first, _ = case.seats
    assert _lease(case, first).json()["data"].get("preparing")
    [machine] = _rooms_machines(case)

    async def enrol():
        async with case.client.test_factory() as db:
            devices = sql_device_service(db)
            device = await devices.approve(
                await devices.start("rooms-cloud"),
                owner_user_id=case.owner_id,
                supply=Supply.cloud,
            )
            await devices.assign_to_project(
                device.device_id, case.project_id, actor_user_id=case.owner_id
            )
            await db.commit()
            return device.device_id

    device_id = case.client.portal.call(enrol)
    _machine_is_up(case, machine, device_id)
    answer = _lease(case, first)
    assert answer.json()["data"]["target"]["device_id"] == device_id, answer.text

    [installed] = [
        call.kwargs["stdin"]
        for call in work_lease.device_hub.exec.await_args_list
        if call.args[0] == device_id
    ]
    configure = ast.parse(installed).body[-1].value
    payload = json.loads(ast.literal_eval(configure.args[0].args[0]))
    # With the limits it runs under, the backend's own.
    assert payload["sandbox"] == {
        "memory_mb": settings.cloud_sandbox_memory_mb,
        "swap_mb": settings.cloud_sandbox_swap_mb,
        "cpus": settings.cloud_sandbox_cpus,
        "pids": settings.cloud_sandbox_pids,
    }


def test_a_second_agent_waits_for_the_machine_the_room_is_renting(cloud_room):
    case = cloud_room
    first, second = case.seats
    assert _lease(case, first).json()["data"].get("preparing")

    waiting = _lease(case, second)
    assert waiting.status_code == 200, waiting.text
    assert waiting.json()["data"].get("preparing") is True
    assert len(case.cloud.created) == 1
    [machine] = _rooms_machines(case)
    _machine_is_up(case, machine, "rooms-machine")
    assert _lease(case, second).json()["data"]["target"]["device_id"] == (
        "rooms-machine"
    )


async def _seat_both_on(client, case):
    """Both sessions hold a ready lease on the room's machine, which the first
    one rented."""
    async with client.test_factory() as db:
        devices = sql_device_service(db)
        rooms = await devices.approve(
            await devices.start("rooms-cloud"),
            owner_user_id=case.owner_id,
            supply=Supply.cloud,
        )
        await devices.assign_to_project(
            rooms.device_id, case.project_id, actor_user_id=case.owner_id
        )
        device_id = rooms.device_id
        new = await devices.approve(
            await devices.start("new"),
            owner_user_id=case.owner_id,
            supply=Supply.self_hosted,
        )
        await devices.assign_to_project(
            new.device_id, case.project_id, actor_user_id=case.owner_id
        )
        topic = await db.get(Topic, case.room_id)
        resource = str(topic.resource_id or case.room_id)
        leases = {}
        for session_id, _ in case.seats:
            row = await AgentSessionService(db).by_id(session_id)
            generation = str(uuid.uuid4())
            row.execution_request = {
                "generation": generation,
                "choice": standard_choice("cloud").model_dump(),
                "authorized_by": None,
            }
            row.work_lease = leases[session_id] = {
                "kind": "device",
                "session_id": str(session_id),
                "generation": generation,
                "resource_id": str(uuid.uuid4()),
                "room_resource_id": resource,
                "device_id": device_id,
                "status": "ready",
                "home": f"/{session_id}",
                "state": f"/{session_id}/state",
                "workspace": f"/{session_id}/work",
                "mcp_servers": [],
            }
        machine = await ProjectMachineRepository(db).add(
            project_id=case.project_id,
            topic_id=case.room_id,
            session_id=case.seats[0][0],
            machine_id=71,
            customer_id=7,
            account_id=9,
            offering_id=1,
            hostname="rooms-cloud",
            login_user="cheese",
            cores=2,
            memory_mb=4096,
            disk_gb=20,
            status=MachineStatus.running,
            ip=None,
            requested_by="alice",
        )
        machine.device_id = device_id
        await db.commit()
        return machine.id, new.device_id, leases


@pytest.mark.anyio
@pytest.mark.parametrize("reachable", [True, False])
async def test_the_rooms_machine_goes_only_after_every_session_on_it_pushed(
    client, cloud_room, monkeypatch, reachable
):
    case = cloud_room
    case.cloud.machines[71] = {"id": 71, "status": "running"}
    machine_id, new_device, _ = await _seat_both_on(client, case)
    events = []

    async def call(lease, method, *args, **kwargs):
        if method == "ping":
            return running()
        events.append(("push", lease["session_id"]))
        return PUSHED

    delete = case.cloud.delete_machine

    async def deleted(provider_id):
        events.append(("delete", provider_id))
        await delete(provider_id)

    case.cloud.delete_machine = deleted
    monkeypatch.setattr(execution, "call", AsyncMock(side_effect=call))
    monkeypatch.setattr(
        work_lease,
        "device_hub",
        SimpleNamespace(
            target=lambda _device: "linux-amd64",
            is_online=lambda device: reachable,
            reconnecting=lambda device: False,
        ),
    )
    person = {
        "Authorization": "Bearer " + create_access_token(case.owner_id, handle="alice")
    }

    moved = client.put(
        f"/topics/{case.room_id}/compute-profile",
        headers=person,
        json={
            "choice": {"name": "New", "profile": "device", "device_id": new_device},
            "abandon_unpushed": not reachable,
        },
    )

    assert moved.status_code == 200, moved.text
    async with client.test_factory() as db:
        left = await ProjectMachineRepository(db).get(machine_id)
    if not reachable:
        # Nobody pushed: what both sessions did is only there, so the machine
        # stays for the room's cleanup.
        assert events == []
        assert left.released_at is None
        return
    pushes = [e for e in events if e[0] == "push"]
    assert {session for _, session in pushes} == {str(s) for s, _ in case.seats}
    assert events[-1] == ("delete", 71), events
    assert events.count(("delete", 71)) == 1
    assert left.released_at is not None
