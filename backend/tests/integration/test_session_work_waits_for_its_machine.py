"""A tool issued while its session's cloud sandbox is prepared waits for it.

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

from app.core.config import settings
from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent.compute_configs import standard_choice
from app.domain.agent_session.services import AgentSessionService
from app.domain.identity.services import IdentityService
from app.domain.machine import session_work as work_lease
from app.domain.machine.models import (
    MAX_ENROLL_ATTEMPTS,
    MAX_PROVIDER_ERRORS,
    AiStatus,
    CloudHost,
    MachineStatus,
)
from app.domain.machine.repositories import CloudHostRepository
from app.domain.project.models import Project
from app.domain.team.models import Team
from app.domain.topic.models import Topic
from tests.integration.conftest import post_project, session_auth_headers
from tests.microcloud import FakeMicroCloud


@pytest.fixture
def cloud_rooms(client, monkeypatch):
    """Two Cloud rooms of one project: an older one whose session already
    works in a sandbox, and a new one whose session has not run anything yet.
    A host has one slot here, so each session's sandbox needs a host of its
    own."""
    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    monkeypatch.setattr(settings, "microcloud_default_cores", 1)
    monkeypatch.setattr(settings, "cloud_host_slots_per_core", 1)
    # No host ahead of demand: the hosts here are the ones these sessions ask for.
    monkeypatch.setattr(settings, "cloud_pool_min_free_slots", 0)
    cloud = FakeMicroCloud()
    monkeypatch.setattr("app.domain.machine.services.MicroCloudClient", lambda: cloud)
    project = post_project(client, json={"name": "Cloud rooms"}, owner="alice").json()[
        "data"
    ]
    project_id = uuid.UUID(project["id"])
    rooms = [
        uuid.UUID(
            client.post(
                "/topics",
                json={
                    "project_id": project["id"],
                    "title": title,
                },
                headers=session_auth_headers("alice"),
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
                topic.compute_config = standard_choice("cloud").model_dump()
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
        reconnecting=lambda device: False,
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


def _session_machine(case, session_id) -> CloudHost | None:
    """The host the session's sandbox is placed on."""

    async def read():
        async with case.client.test_request_factory() as db:
            repo = CloudHostRepository(db)
            home = await repo.current_home(session_id)
            return None if home is None else await repo.get(home.host_id)

    return case.client.portal.call(read)


def _machine_is_up(case, session_id, device_id):
    """What the pool sweep does once the host's connector dials in."""

    async def enrol():
        async with case.client.test_request_factory() as db:
            repo = CloudHostRepository(db)
            home = await repo.current_home(session_id)
            host = await repo.get(home.host_id)
            case.cloud.machines[host.machine_id].update(
                status="running", aiStatus="disabled"
            )
            host.status = MachineStatus.running
            host.ai_status = AiStatus.disabled
            host.last_seen_at = datetime.now(UTC)
            await repo.mark_enrolled(host, device_id=device_id, when=datetime.now(UTC))
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

    # The new room's first command: the older room's host is full, so the
    # pool asks for another, and a bounded request answers that the sandbox is
    # still being prepared.
    waiting = _lease(case, case.new, timeout=5)
    assert waiting.status_code == 200, waiting.text
    answer = waiting.json()["data"]
    assert answer["preparing"] is True
    assert "target" not in answer
    own = _session_machine(case, case.new[1])
    assert own is not None and own.device_id is None
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


def test_a_machine_that_drops_while_it_is_set_up_is_still_being_prepared(
    cloud_rooms, monkeypatch
):
    """The incident behind it: a room's freshly claimed machine lost its link
    as its executor was being installed, and the agent was answered with a raw
    409 and the word offline, seven seconds before the machine was back. That
    is the machine still being prepared, and the command runs once it is."""
    from app.domain.agent.device_hub import DeviceOffline

    case = cloud_rooms
    monkeypatch.setattr(work_lease, "PREPARING_WAIT_S", 30.0)
    assert _lease(case, case.new, timeout=0.001).json()["data"]["preparing"]
    _machine_is_up(case, case.new[1], "new-rooms-machine")

    install = case.hub.exec.side_effect

    async def drops_once(device, argv, **kwargs):
        if case.hub.exec.await_count == 1:
            case.online.discard(device)
            raise DeviceOffline(device)
        return await install(device, argv, **kwargs)

    case.hub.exec.side_effect = drops_once
    with ThreadPoolExecutor(max_workers=1) as requests:
        pending = requests.submit(_lease, case, case.new)
        time.sleep(1.5)
        assert not pending.done(), "The command waits for the machine to return"
        case.online.add("new-rooms-machine")
        answer = pending.result(timeout=20)
    assert answer.status_code == 200, answer.text
    assert answer.json()["data"]["target"]["device_id"] == "new-rooms-machine"


def test_a_session_that_will_not_wait_is_still_answered(cloud_rooms):
    """A session relaunched onto its machine takes it without waiting: it asks
    with the smallest timeout the route accepts. That caps the wait, not the
    answer: it is told where its machine is, or that it is still preparing."""
    case = cloud_rooms
    preparing = _lease(case, case.new, timeout=0.001)
    assert preparing.status_code == 200, preparing.text
    assert preparing.json()["data"]["preparing"] is True

    _machine_is_up(case, case.new[1], "new-rooms-machine")
    ready = _lease(case, case.new, timeout=0.001)
    assert ready.status_code == 200, ready.text
    assert ready.json()["data"]["target"]["device_id"] == "new-rooms-machine"


def test_a_host_that_cannot_be_enrolled_is_given_up_for_another(
    cloud_rooms, monkeypatch
):
    """Nothing of the session's is on a host that never joined, so the pool
    lets it go and prepares the sandbox on another instead of reporting the
    failure for good."""
    case = cloud_rooms
    monkeypatch.setattr(work_lease, "PREPARING_WAIT_S", 1.0)
    assert _lease(case, case.new, timeout=5).json()["data"]["preparing"] is True
    machine = _session_machine(case, case.new[1])

    async def fail():
        async with case.client.test_request_factory() as db:
            row = await db.get(CloudHost, machine.id)
            case.cloud.machines[row.machine_id].update(
                status="running", aiStatus="disabled"
            )
            row.status = MachineStatus.running
            row.ai_status = AiStatus.disabled
            row.enroll_attempts = MAX_ENROLL_ATTEMPTS
            await db.commit()

    case.client.portal.call(fail)
    answer = _lease(case, case.new, timeout=5).json()["data"]
    assert answer["preparing"] is True, answer
    assert case.cloud.deleted == [machine.machine_id]
    assert _session_machine(case, case.new[1]).id != machine.id
    assert len(case.cloud.created) == 2
    case.hub.exec.assert_not_awaited()


def _provider_fails(case, session_id):
    """MicroCloud gives up building the session's current machine."""
    machine = _session_machine(case, session_id)
    case.cloud.machines[machine.machine_id]["status"] = "error"
    return machine.machine_id


def test_a_machine_the_provider_failed_to_build_is_replaced(cloud_rooms, monkeypatch):
    """dev, 2026-10-01: a Proxmox create outlived MicroCloud's wait, and every
    later command of the room was answered 「创建失败」 for good. The pool lets
    the failed host go and places the sandbox again."""
    case = cloud_rooms
    monkeypatch.setattr(work_lease, "PREPARING_WAIT_S", 1.0)
    assert _lease(case, case.new, timeout=5).json()["data"]["preparing"] is True
    failed = _provider_fails(case, case.new[1])

    again = _lease(case, case.new, timeout=5)
    assert again.status_code == 200, again.text
    assert again.json()["data"]["preparing"] is True, again.text
    assert case.cloud.deleted == [failed]
    assert _session_machine(case, case.new[1]).machine_id != failed

    _machine_is_up(case, case.new[1], "second-machine")
    ready = _lease(case, case.new, timeout=5)
    assert ready.json()["data"]["target"]["device_id"] == "second-machine"
    assert len(case.cloud.created) == 2


def test_a_session_whose_hosts_keep_failing_is_told_retries_were_made(
    cloud_rooms, monkeypatch
):
    case = cloud_rooms
    monkeypatch.setattr(work_lease, "PREPARING_WAIT_S", 1.0)
    for _ in range(MAX_PROVIDER_ERRORS):
        answer = _lease(case, case.new, timeout=5).json()["data"]
        assert answer["preparing"] is True, answer
        _provider_fails(case, case.new[1])

    monkeypatch.setattr(work_lease, "PREPARING_WAIT_S", 30.0)
    started = time.monotonic()
    data = _lease(case, case.new).json()["data"]
    assert time.monotonic() - started < 5, "A failure is not waited out"
    assert "preparing" not in data
    assert data["unavailable"].startswith("沙箱准备失败")
    assert f"连续 {MAX_PROVIDER_ERRORS} 次" in data["unavailable"]
    assert "重试" in data["unavailable"]
    assert len(case.cloud.created) == MAX_PROVIDER_ERRORS
    assert len(case.cloud.deleted) == MAX_PROVIDER_ERRORS
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


def test_each_session_on_a_cloud_host_has_its_executor_sandboxed(cloud_rooms):
    """A cloud host's sessions are `isolated`: each one's executor is installed
    to run in a sandbox of its own (#2320)."""
    import ast

    from app.domain.device.supply import Supply
    from app.domain.device.wiring import sql_device_service
    from app.domain.machine.models import HOST_OWNER

    case = cloud_rooms
    assert _lease(case, case.new, timeout=5).json()["data"].get("preparing")

    async def enrol():
        async with case.client.test_factory() as db:
            owner = await IdentityService(db).ensure_agent_user(handle=HOST_OWNER)
            devices = sql_device_service(db)
            device = await devices.approve(
                await devices.start("pool-host"),
                owner_user_id=owner.id,
                supply=Supply.cloud,
            )
            await db.commit()
            return device.device_id

    device_id = case.client.portal.call(enrol)
    _machine_is_up(case, case.new[1], device_id)
    answer = _lease(case, case.new)
    assert answer.json()["data"]["target"]["device_id"] == device_id, answer.text

    [installed] = [
        call.kwargs["stdin"]
        for call in case.hub.exec.await_args_list
        if call.args[0] == device_id
    ]
    configure = ast.parse(installed).body[-1].value
    payload = json.loads(ast.literal_eval(configure.args[0].args[0]))
    assert payload["sandbox"] is True
