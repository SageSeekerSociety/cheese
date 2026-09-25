"""A tool issued while its session's Cloud machine is prepared waits for it.

The room's first command used to come back at once with "still preparing",
and the agent retried it in a loop until the machine was up. A native session
on that machine would simply have run the command once it could.
"""

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent_session.services import AgentSessionService
from app.domain.identity.services import IdentityService
from app.domain.machine import session_work as work_lease
from app.domain.machine.models import MAX_ENROLL_ATTEMPTS, ProjectMachine
from app.domain.machine.repositories import ProjectMachineRepository
from app.domain.machine.services import MachineService
from app.domain.project.models import Project
from app.domain.team.models import Team
from app.domain.topic.models import Topic
from tests.integration.conftest import post_project
from tests.unit.test_machine_service import FakeMicroCloud


@pytest.fixture
def cloud_rooms(client, monkeypatch):
    """Two Cloud rooms of one project: an older one whose session already
    works on its own machine, and a new one whose session has not run
    anything yet."""
    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    cloud = FakeMicroCloud()
    monkeypatch.setattr(
        work_lease, "MachineService", lambda db: MachineService(db, cloud)
    )
    project = post_project(
        client, json={"name": "Cloud rooms", "owner_handle": "alice"}
    ).json()["data"]
    project_id = uuid.UUID(project["id"])
    rooms = [
        uuid.UUID(
            client.post(
                "/topics",
                json={
                    "project_id": project["id"],
                    "title": title,
                    "created_by": "alice",
                },
            ).json()["data"]["id"]
        )
        for title in ("Older room", "New room")
    ]

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
            seats = []
            for room_id in rooms:
                topic = await db.get(Topic, room_id)
                topic.compute_profile = "cloud"
                agent = await IdentityService(db).ensure_room_agent_user(room_id)
                row = await AgentSessionService(db).ensure(
                    room_id, "cheese", harness="claude-code"
                )
                resource = str(topic.resource_id or room_id)
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
                seats.append((room_id, row.id, token))
            await db.commit()
            return seats

    older, new = client.portal.call(seed)
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
        is_online=lambda device: device in online,
        exec=AsyncMock(side_effect=install),
    )
    monkeypatch.setattr(work_lease, "device_hub", hub)
    monkeypatch.setattr(execution, "call", AsyncMock(return_value={}))
    return SimpleNamespace(
        client=client,
        cloud=cloud,
        hub=hub,
        online=online,
        older=older,
        new=new,
    )


def _lease(case, seat, timeout=660):
    room_id, session_id, token = seat
    return case.client.post(
        f"/topics/{room_id}/sessions/{session_id}/work-lease",
        headers={"X-Cheese-Token": token},
        json={"env": {}, "timeout": timeout},
    )


def _session_machine(case, session_id):
    async def read():
        async with case.client.test_request_factory() as db:
            return await db.scalar(
                select(ProjectMachine).where(ProjectMachine.session_id == session_id)
            )

    return case.client.portal.call(read)


def _machine_is_up(case, session_id, device_id):
    """What the enrolment sweep does once the connector on it dials in."""

    async def enrol():
        async with case.client.test_request_factory() as db:
            repo = ProjectMachineRepository(db)
            machine = await repo.get_active_for_session(session_id)
            case.cloud.machines[machine.machine_id].update(
                status="running", aiStatus="ready"
            )
            await repo.mark_enrolled(
                machine, device_id=device_id, when=datetime.now(UTC)
            )
            await db.commit()

    case.client.portal.call(enrol)
    case.online.add(device_id)


def test_a_new_rooms_first_command_waits_for_its_own_machine(cloud_rooms, monkeypatch):
    case = cloud_rooms
    monkeypatch.setattr(work_lease, "PREPARING_WAIT_S", 1.0)
    # The older room's session works on its own machine, online right now.
    first = _lease(case, case.older, timeout=5)
    assert first.json()["data"].get("preparing"), first.text
    _machine_is_up(case, case.older[1], "older-rooms-machine")
    assert _lease(case, case.older).json()["data"]["target"]["device_id"] == (
        "older-rooms-machine"
    )

    # The new room's first command: the platform asks for the new room's own
    # machine, and a bounded request answers that it is still being prepared —
    # never with the older room's machine, online as it is.
    waiting = _lease(case, case.new, timeout=5)
    assert waiting.status_code == 200, waiting.text
    answer = waiting.json()["data"]
    assert answer["preparing"] is True
    assert "target" not in answer
    own = _session_machine(case, case.new[1])
    assert own is not None and own.topic_id == case.new[0]
    assert len(case.cloud.created) == 2

    # The next ask waits, and runs as soon as that machine is up.
    monkeypatch.setattr(work_lease, "PREPARING_WAIT_S", 30.0)
    with ThreadPoolExecutor(max_workers=1) as requests:
        started = time.monotonic()
        pending = requests.submit(_lease, case, case.new)
        time.sleep(1.5)
        assert not pending.done(), "The command must wait for its machine"
        _machine_is_up(case, case.new[1], "new-rooms-machine")
        ready = pending.result(timeout=20)
    assert ready.status_code == 200, ready.text
    target = ready.json()["data"]["target"]
    assert target["device_id"] == "new-rooms-machine"
    assert time.monotonic() - started < 10, "Ready is noticed within a poll or two"
    installed_on = [call.args[0] for call in case.hub.exec.await_args_list]
    assert installed_on == ["older-rooms-machine", "new-rooms-machine"]


@pytest.mark.parametrize("failure", ["provider-error", "enrolment-exhausted"])
def test_a_machine_that_cannot_be_prepared_is_reported_at_once(
    cloud_rooms, monkeypatch, failure
):
    case = cloud_rooms
    monkeypatch.setattr(work_lease, "PREPARING_WAIT_S", 1.0)
    assert _lease(case, case.new, timeout=5).json()["data"]["preparing"] is True
    machine = _session_machine(case, case.new[1])

    async def fail():
        async with case.client.test_request_factory() as db:
            row = await db.get(ProjectMachine, machine.id)
            if failure == "provider-error":
                case.cloud.machines[row.machine_id]["status"] = "error"
            else:
                case.cloud.machines[row.machine_id].update(
                    status="running", aiStatus="ready"
                )
                row.enroll_attempts = MAX_ENROLL_ATTEMPTS
            await db.commit()

    case.client.portal.call(fail)
    monkeypatch.setattr(work_lease, "PREPARING_WAIT_S", 30.0)
    started = time.monotonic()
    answer = _lease(case, case.new)
    assert time.monotonic() - started < 5, "A failure is not waited out"
    data = answer.json()["data"]
    assert "preparing" not in data
    assert data["unavailable"].startswith(
        "Cloud 机器创建失败" if failure == "provider-error" else "Cloud 机器接入失败"
    )
    case.hub.exec.assert_not_awaited()


def test_a_caller_that_left_stops_the_wait_without_taking_the_machine(
    cloud_rooms,
):
    """The command was stopped (or ran out of its own time) while waiting: the
    wait ends then, and nothing is installed on the machine for it."""
    case = cloud_rooms
    room_id, session_id, token = case.new
    from app.core.sandbox_auth import scoped_token_claims

    left = {"at": None}

    async def gone():
        return left["at"] is not None and time.monotonic() >= left["at"]

    async def ask():
        async with case.client.test_request_factory() as db:
            return await work_lease.ensure(
                db,
                topic_id=room_id,
                session_id=session_id,
                claims=scoped_token_claims(token),
                token=token,
                env={},
                wait_s=30.0,
                gone=gone,
            )

    left["at"] = time.monotonic() + 1.0
    started = time.monotonic()
    answer = case.client.portal.call(ask)
    assert time.monotonic() - started < 5
    assert answer["preparing"] is True
    _machine_is_up(case, session_id, "new-rooms-machine")
    case.hub.exec.assert_not_awaited()

    async def lease():
        async with case.client.test_request_factory() as db:
            return (await AgentSessionService(db).by_id(session_id)).work_lease

    assert case.client.portal.call(lease) is None
