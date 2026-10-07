"""An idle cloud sandbox is destroyed, and its session's next tool call gets a
new one.

Nothing of a destroyed sandbox is kept: its home is deleted from the host and
its slot is free for anyone. The next tool call places the session in a new
sandbox, from git, and tells the agent once. A tool call that arrives while
the home is being deleted waits for it to go.

The hosts here are directories on this machine: what the platform runs on a
host (``machine/sandbox_home.py``) runs here for real, with ``HOME`` pointed
at the host's directory.
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent.compute_configs import standard_choice
from app.domain.agent.device_hub import device_hub
from app.domain.agent.models import AgentTurn
from app.domain.agent_session.services import AgentSessionService
from app.domain.identity.services import IdentityService
from app.domain.machine import lifecycle
from app.domain.machine import session_work as work_lease
from app.domain.machine.models import AiStatus, CloudHost, CloudHostHome, MachineStatus
from app.domain.machine.repositories import CloudHostRepository
from app.domain.machine.runner import SandboxSweeper
from app.domain.machine.sandbox_wait import SANDBOX_LOST
from app.domain.machine.services import HostPool
from app.domain.machine.session_work import checkpoint_room
from app.domain.project.models import Project
from app.domain.room_task.models import Task
from app.domain.run_record.models import RunRecord
from app.domain.team.models import Team
from app.domain.topic.models import Topic, TopicStatus
from tests.integration.conftest import post_project, session_auth_headers
from tests.microcloud import FakeMicroCloud


class Hosts:
    """The cloud hosts' connectors. A host program runs here, in the host's
    directory; an executor install is answered as one that started."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.online: set[str] = set()
        self.installs: list[str] = []
        # What each host program was asked to do, in order: (host, action).
        self.actions: list[tuple[str, str]] = []
        # Called with (host, action) before a host program runs, and after.
        self.before = None
        self.after = None
        # Commands each host's executors report running in the background.
        self.background: dict[str, int] = {}

    def is_online(self, device_id):
        return device_id in self.online

    def reconnecting(self, device_id):
        return False

    def home_dir(self, device_id: str) -> Path:
        path = self.root / device_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def exec(self, device_id, argv, *, stdin=None, timeout=60, **_kwargs):
        if stdin and "def main(cleanup, request)" in stdin:
            action = json.loads(
                stdin.rsplit("json.loads(", 1)[1].rsplit("))", 1)[0][1:-1]
            )["action"]
            self.actions.append((device_id, action))
            if self.before is not None:
                await self.before(device_id, action)
            done = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-"],
                input=stdin,
                text=True,
                capture_output=True,
                env={**os.environ, "HOME": str(self.home_dir(device_id))},
                timeout=timeout,
            )
            if self.after is not None:
                await self.after(device_id, action)
            return {
                "exit": done.returncode,
                "stdout": done.stdout,
                "stderr": done.stderr,
            }
        self.installs.append(device_id)
        return {
            "exit": 0,
            "stdout": json.dumps(
                {
                    "state": f"/{device_id}/state",
                    "workspace": f"/{device_id}/work",
                    "mcp_servers": [],
                }
            ),
        }

    async def executor(self, target, method, params, **_kwargs):
        if method == "ping":
            return {"running_commands": self.background.get(target["device_id"], 0)}
        if method == "control":
            # A push that went through, as `cheese sync --all` reports one.
            return {"value": {"stdout": "", "stderr": "", "interrupted": False}}
        return {}


@pytest.fixture
def cloud(client, monkeypatch, tmp_path):
    """Three Cloud rooms of one project, each with one agent session. A host
    runs one sandbox at a time."""
    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    monkeypatch.setattr(settings, "microcloud_default_cores", 1)
    monkeypatch.setattr(settings, "microcloud_default_disk_gb", 20)
    monkeypatch.setattr(settings, "cloud_host_slots_per_core", 1)
    monkeypatch.setattr(settings, "cloud_pool_min_free_slots", 0)
    monkeypatch.setattr(settings, "cloud_host_idle_hold_s", 0)
    monkeypatch.setattr(settings, "cloud_sandbox_idle_stop_s", 600)
    monkeypatch.setattr(settings, "cloud_sandbox_background_cap_s", 3600)
    provider = FakeMicroCloud()
    monkeypatch.setattr(
        "app.domain.machine.services.MicroCloudClient", lambda: provider
    )
    hosts = Hosts(tmp_path / "hosts")
    monkeypatch.setattr(work_lease, "device_hub", hosts)
    monkeypatch.setattr(lifecycle, "device_hub", hosts)
    monkeypatch.setattr(device_hub, "is_online", hosts.is_online)
    monkeypatch.setattr(execution, "call", hosts.executor)
    # A tool call waiting for its sandbox answers "preparing" this soon.
    monkeypatch.setattr(work_lease, "PREPARING_WAIT_S", 0.5)
    lifecycle._asked.clear()

    project = post_project(client, json={"name": "Sleepy"}, owner="alice").json()[
        "data"
    ]
    project_id = uuid.UUID(project["id"])
    rooms = [
        uuid.UUID(
            client.post(
                "/topics",
                json={"project_id": project["id"], "title": title},
                headers=session_auth_headers("alice"),
            ).json()["data"]["id"]
        )
        for title in ("One", "Two", "Three")
    ]

    async def seed():
        async with client.test_request_factory() as db:
            team = Team(
                name="Sleepy team",
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
                seats.append(SimpleNamespace(room=room_id, session=row.id, token=token))
            await db.commit()
            return seats

    seats = client.portal.call(seed)
    yield SimpleNamespace(
        client=client,
        provider=provider,
        hosts=hosts,
        project_id=project_id,
        seats=seats,
    )


def run(case, coroutine_fn):
    return case.client.portal.call(coroutine_fn)


def tool_call(case, seat, timeout=5, *, tells_agent=False):
    """What a tool of the session asks first: its hands. ``tells_agent``: the
    caller puts what the platform says beside the tool's result, as a tool
    the agent called does."""
    return case.client.post(
        f"/topics/{seat.room}/sessions/{seat.session}/work-lease",
        headers={"X-Cheese-Token": seat.token},
        json={"env": {}, "timeout": timeout, "tells_agent": tells_agent},
    ).json()["data"]


def home_of(case, seat) -> CloudHostHome | None:
    async def read():
        async with case.client.test_request_factory() as db:
            return await CloudHostRepository(db).current_home(seat.session)

    return run(case, read)


def host_of(case, seat) -> CloudHost | None:
    async def read():
        async with case.client.test_request_factory() as db:
            repo = CloudHostRepository(db)
            home = await repo.current_home(seat.session)
            return None if home is None else await repo.get(home.host_id)

    return run(case, read)


def host_comes_up(case, seat, device_id):
    """What the pool sweep does once the host's connector dials in."""

    async def enrol():
        async with case.client.test_request_factory() as db:
            repo = CloudHostRepository(db)
            home = await repo.current_home(seat.session)
            host = await repo.get(home.host_id)
            case.provider.machines[host.machine_id].update(
                status="running", aiStatus="disabled"
            )
            host.status = MachineStatus.running
            host.ai_status = AiStatus.disabled
            host.last_seen_at = datetime.now(UTC)
            await repo.mark_enrolled(host, device_id=device_id, when=datetime.now(UTC))
            await db.commit()

    run(case, enrol)
    case.hosts.online.add(device_id)


def working_on(case, seat, device_id) -> Path:
    """The session gets its sandbox on a new host called ``device_id``, and
    leaves work in it: a file it has not committed anywhere. Returns its
    home on that host."""
    assert tool_call(case, seat).get("preparing")
    host_comes_up(case, seat, device_id)
    assert tool_call(case, seat)["target"]["device_id"] == device_id
    home = sandbox_dir(case, seat, device_id)
    (home / "room").mkdir(parents=True, exist_ok=True)
    (home / "room" / "notes.md").write_text("not committed anywhere\n")
    return home


def sandbox_dir(case, seat, device_id) -> Path:
    resource = home_of(case, seat).resource_id
    return (
        case.hosts.home_dir(device_id)
        / ".cheese"
        / "home"
        / str(case.project_id)
        / resource
    )


def time_passes(case, seat, idle: timedelta):
    """The session last did anything ``idle`` ago."""

    async def age():
        async with case.client.test_request_factory() as db:
            await db.execute(
                update(CloudHostHome)
                .where(CloudHostHome.session_id == seat.session)
                .values(active_at=datetime.now(UTC) - idle)
            )
            await db.commit()

    run(case, age)


def sweep(case) -> dict:
    return run(case, SandboxSweeper(case.client.test_request_factory).sweep)


def maintain(case):
    async def go():
        async with case.client.test_request_factory() as db:
            await HostPool(db, case.provider).maintain()
            await db.commit()

    run(case, go)


def room_lines(case, seat) -> list[str]:
    async def read():
        async with case.client.test_request_factory() as db:
            return list(
                await db.scalars(
                    select(RunRecord.content)
                    .where(
                        RunRecord.conversation_id == seat.room,
                        RunRecord.kind.in_(
                            ["cloud_startup", "cloud_provisioning", "sandbox_asleep"]
                        ),
                    )
                    .order_by(RunRecord.created_at)
                )
            )

    return run(case, read)


def room_cleanup_after_its_home_left(case, seat, resource, monkeypatch) -> dict:
    """The room is archived and its cleanup is due. The sandbox was destroyed
    and its host released before the cleanup reached it, but the cleanup
    recorded the home on that host when it took its inventory — the rooms
    PR #2749 left waiting on "device … is offline". Returns what one sweep of
    room cleanups answered."""
    from app.domain.topic import retire
    from app.domain.topic.services import TopicService

    monkeypatch.setattr(settings, "topic_archive_cleanup_delay_s", 0)

    async def archive():
        async with case.client.test_request_factory() as db:
            await TopicService(db).archive(seat.room, by="alice")
            await db.commit()

    run(case, archive)

    async def recorded(_session, _operation, _inventory):
        return [{"kind": "device", "device_id": "host-a", "resource_id": resource}]

    async def exec_(device_id, argv, *, stdin=None, timeout=60, **kwargs):
        if not case.hosts.is_online(device_id):
            raise RuntimeError(f"device {device_id} is offline")
        return await case.hosts.exec(
            device_id, argv, stdin=stdin, timeout=timeout, **kwargs
        )

    monkeypatch.setattr(retire, "_inventory", recorded)
    monkeypatch.setattr(retire.device_hub, "exec", exec_)
    return run(
        case,
        lambda: retire.sweep_retired_storage(
            case.client.test_request_factory, checkpoint=checkpoint_room
        ),
    )


def cleanup_of(case, seat) -> dict:
    return case.client.get(
        f"/topics/{seat.room}/cleanup", headers=session_auth_headers("alice")
    ).json()["data"]


def host_of_machine(case, device_id) -> int:
    async def read():
        async with case.client.test_request_factory() as db:
            return await db.scalar(
                select(CloudHost.machine_id).where(CloudHost.device_id == device_id)
            )

    return run(case, read)


def turn_runs(case, conversation_id):
    async def go():
        async with case.client.test_request_factory() as db:
            db.add(
                AgentTurn(
                    id=uuid.uuid4(),
                    conversation_id=conversation_id,
                    continuation_id=uuid.uuid4(),
                    author="alice",
                    started_at=datetime.now(UTC),
                )
            )
            await db.commit()

    run(case, go)


def test_an_idle_sandbox_is_destroyed_and_the_next_tool_call_gets_a_new_one(cloud):
    seat = cloud.seats[0]
    home = working_on(cloud, seat, "host-a")
    # A dev server the agent started and left running.
    server = subprocess.Popen(["sleep", "600"], cwd=home / "room")
    try:
        time_passes(cloud, seat, timedelta(minutes=11))
        assert sweep(cloud)["destroyed"] == 1
        assert server.wait(timeout=30) is not None
    finally:
        server.kill()
    assert not home.exists()
    assert home_of(cloud, seat) is None
    assert room_lines(cloud, seat)[-1].startswith("环境 11 分钟没有活动，已释放。")

    answer = tool_call(cloud, seat, tells_agent=True)
    assert answer["target"]["device_id"] == "host-a"
    assert answer["notice"] == SANDBOX_LOST
    assert not (home / "room" / "notes.md").exists()
    # Told once.
    assert "notice" not in tool_call(cloud, seat, tells_agent=True)


def test_a_sandbox_is_kept_while_its_conversation_runs_a_turn_or_it_was_just_used(
    cloud,
):
    seat = cloud.seats[0]
    home = working_on(cloud, seat, "host-a")

    # Asked for a tool five minutes ago.
    time_passes(cloud, seat, timedelta(minutes=5))
    assert sweep(cloud)["destroyed"] == 0

    turn_runs(cloud, seat.room)
    # The turn has thought for an hour without asking for a tool.
    time_passes(cloud, seat, timedelta(hours=1))
    assert sweep(cloud)["destroyed"] == 0
    assert (home / "room" / "notes.md").exists()


def test_a_background_command_keeps_a_sandbox_up_only_until_the_cap(cloud):
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")
    cloud.hosts.background["host-a"] = 1

    time_passes(cloud, seat, timedelta(minutes=30))
    assert sweep(cloud)["destroyed"] == 0

    lifecycle._asked.clear()
    time_passes(cloud, seat, timedelta(minutes=61))
    assert sweep(cloud)["destroyed"] == 1


def test_a_destroyed_sandbox_frees_its_slot_for_another_session(cloud):
    one, two, _ = cloud.seats
    working_on(cloud, one, "host-a")
    # Host A runs one sandbox at a time.
    assert tool_call(cloud, two).get("preparing")
    assert host_of(cloud, two).id != host_of_machine_row(cloud, "host-a")

    time_passes(cloud, one, timedelta(minutes=11))
    assert sweep(cloud)["destroyed"] == 1
    other = home_of(cloud, two)
    assert other is not None

    three = cloud.seats[2]
    assert tool_call(cloud, three)["target"]["device_id"] == "host-a"


def host_of_machine_row(case, device_id):
    async def read():
        async with case.client.test_request_factory() as db:
            return await db.scalar(
                select(CloudHost.id).where(CloudHost.device_id == device_id)
            )

    return run(case, read)


def test_a_tool_call_during_an_unfinished_removal_waits_and_then_gets_a_new_sandbox(
    cloud,
):
    seat = cloud.seats[0]
    home = working_on(cloud, seat, "host-a")

    async def host_fails(device_id, action):
        if action == "destroy":
            raise RuntimeError("the host dropped the call")

    cloud.hosts.before = host_fails
    time_passes(cloud, seat, timedelta(minutes=11))
    assert sweep(cloud)["destroyed"] == 0
    # The removal did not finish: the call waits rather than start a sandbox
    # over a directory half deleted.
    assert tool_call(cloud, seat).get("preparing")
    assert home.exists()

    cloud.hosts.before = None

    async def later():
        async with cloud.client.test_request_factory() as db:
            await db.execute(
                update(CloudHostHome)
                .where(CloudHostHome.session_id == seat.session)
                .values(stopped_at=datetime.now(UTC) - lifecycle.STOP_HOLD)
            )
            await db.commit()

    run(cloud, later)
    assert sweep(cloud)["destroyed"] == 1
    assert not home.exists()
    answer = tool_call(cloud, seat, tells_agent=True)
    assert answer["target"]["device_id"] == "host-a"
    assert answer["notice"] == SANDBOX_LOST


def test_a_conversation_mid_turn_does_not_keep_other_sandboxes(cloud, monkeypatch):
    monkeypatch.setattr(lifecycle, "DESTROYS_PER_SWEEP", 1)
    busy, quiet, _ = cloud.seats
    working_on(cloud, busy, "host-a")
    working_on(cloud, quiet, "host-b")

    turn_runs(cloud, busy.room)
    time_passes(cloud, busy, timedelta(hours=1))
    time_passes(cloud, quiet, timedelta(minutes=11))

    assert sweep(cloud)["destroyed"] == 1
    assert home_of(cloud, quiet) is None
    assert home_of(cloud, busy) is not None


def test_a_task_at_work_in_the_room_does_not_keep_another_sessions_sandbox(cloud):
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")

    async def task_turn_runs():
        async with cloud.client.test_request_factory() as db:
            task = Task(project_id=cloud.project_id, room_id=seat.room, title="Other")
            db.add(task)
            await db.flush()
            db.add(
                AgentTurn(
                    id=uuid.uuid4(),
                    conversation_id=task.id,
                    continuation_id=uuid.uuid4(),
                    author="alice",
                    started_at=datetime.now(UTC),
                )
            )
            await db.commit()

    run(cloud, task_turn_runs)
    time_passes(cloud, seat, timedelta(minutes=11))

    assert sweep(cloud)["destroyed"] == 1
    assert home_of(cloud, seat) is None


def test_an_archived_rooms_sandbox_is_destroyed_and_its_host_released(cloud):
    """An archived room whose cleanup has not finished still had its sandbox
    running; it goes like any other, and its host with it."""
    archived_room, other, _ = cloud.seats
    working_on(cloud, archived_room, "host-a")
    working_on(cloud, other, "host-b")

    async def archive():
        async with cloud.client.test_request_factory() as db:
            (await db.get(Topic, archived_room.room)).status = TopicStatus.archived
            await db.commit()

    run(cloud, archive)
    machine = host_of_machine(cloud, "host-a")
    time_passes(cloud, archived_room, timedelta(minutes=11))
    time_passes(cloud, other, timedelta(minutes=11))

    assert sweep(cloud)["destroyed"] == 2
    maintain(cloud)
    assert machine in cloud.provider.deleted


def test_a_room_cleanup_does_not_wait_on_the_host_of_a_destroyed_sandbox(
    cloud, monkeypatch
):
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")
    resource = home_of(cloud, seat).resource_id
    time_passes(cloud, seat, timedelta(minutes=11))
    assert sweep(cloud)["destroyed"] == 1
    maintain(cloud)
    cloud.hosts.online.discard("host-a")

    assert room_cleanup_after_its_home_left(cloud, seat, resource, monkeypatch) == {
        "completed": 1,
        "pending": 0,
    }
    assert cleanup_of(cloud, seat)["state"] in {"complete", "retained"}
