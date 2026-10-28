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
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent.device_hub import device_hub
from app.domain.agent.models import AgentTurn
from app.domain.agent_session.services import AgentSessionService
from app.domain.identity.services import IdentityService
from app.domain.machine import lifecycle
from app.domain.machine import session_work as work_lease
from app.domain.machine.models import AiStatus, CloudHost, MachineStatus, SandboxHome
from app.domain.machine.repositories import CloudHostRepository
from app.domain.machine.runner import SandboxSweeper
from app.domain.machine.sandbox_wait import SANDBOX_LOST
from app.domain.machine.services import HostPool
from app.domain.machine.session_work import checkpoint_room
from app.domain.project.models import Project
from app.domain.review.archive import close_cards_for_archived_topic
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.room_task.archive import close_room_tasks
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
        # Stops sent to those commands, in order: (host, command, signal).
        self.signals: list[tuple[str, str, int]] = []

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
        device_id = target["device_id"]
        if method == "ping":
            running = self.background.get(device_id, 0)
            return {
                "running_commands": running,
                # Named, as the executor names them: the sweep stops one that
                # outlasts the cap instead of destroying the sandbox.
                "running_command_ids": [f"task-{index}" for index in range(running)],
            }
        if method == "control":
            if params.get("subtype") == "shell" and params.get("operation") == "signal":
                self.signals.append(
                    (device_id, str(params["command_id"]), int(params["signal"]))
                )
                left = self.background.get(device_id, 0)
                self.background[device_id] = max(0, left - 1)
                return {"value": {"running": False}}
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
                topic.default_environment = {"kind": "sandbox", "spec": "standard"}
                agent = await IdentityService(db).ensure_room_agent_user(room_id)
                row = await AgentSessionService(db).ensure(
                    room_id, "cheese", harness="claude-code"
                )
                resource = str(topic.resource_id or room_id)
                row.runtime_location = {
                    "device_id": "center",
                    "resource_id": resource,
                    "channel": "device",
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
        headers={"Authorization": f"Bearer {seat.token}"},
        json={"env": {}, "timeout": timeout, "tells_agent": tells_agent},
    ).json()["data"]


def home_of(case, seat) -> SandboxHome | None:
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
            await repo.mark_enrolled(
                host,
                device_id=device_id,
                when=datetime.now(UTC),
                sandbox_disk_gb=host.sandbox_disk_gb,
            )
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
                update(SandboxHome)
                .where(SandboxHome.session_id == seat.session)
                .values(active_at=datetime.now(UTC) - idle)
            )
            await db.commit()

    run(case, age)


def sweep(case) -> dict:
    return run(case, SandboxSweeper(case.client.test_request_factory).sweep)


def test_recovery_keeps_its_sandbox_while_other_idle_sandboxes_are_reclaimed(cloud):
    recovering, idle = cloud.seats[:2]
    protected = working_on(cloud, recovering, "recovering-host")
    disposable = working_on(cloud, idle, "idle-host")
    time_passes(cloud, recovering, timedelta(minutes=20))
    time_passes(cloud, idle, timedelta(minutes=20))

    async def inspect():
        sweeper = SandboxSweeper(
            cloud.client.test_request_factory,
            recovering=lambda conversation: conversation == recovering.room,
        )
        return await sweeper.sweep()

    assert run(cloud, inspect)["destroyed"] == 1
    assert protected.exists()
    assert not disposable.exists()


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
    from app.domain.block.adapters import platform_line
    from app.domain.delivery.ledger import settle_card
    from app.domain.room_cleanup import retire
    from app.domain.topic.services import TopicService

    monkeypatch.setattr(settings, "topic_archive_cleanup_delay_s", 0)

    async def archive():
        async with case.client.test_request_factory() as db:
            await TopicService(db).archive(
                seat.room,
                by="alice",
                settle_card=settle_card,
                cards=close_cards_for_archived_topic,
                close_tasks=close_room_tasks,
                events=platform_line,
            )
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


def line_of(case, seat, *, title="A line") -> SimpleNamespace:
    """A task of that room and the session that works in it: the line a delivery
    is handed over on. A task is a place of its own, so its session asks for its
    hands on the task's path, and its sandbox is its own too."""

    async def open_line():
        async with case.client.test_request_factory() as db:
            room = await db.get(Topic, seat.room)
            task = Task(project_id=case.project_id, room_id=seat.room, title=title)
            db.add(task)
            await db.flush()
            handle = (
                await IdentityService(db).ensure_room_agent_user(seat.room)
            ).username
            row = await AgentSessionService(db).ensure(
                task.id, handle, harness="claude-code"
            )
            resource = str(room.resource_id or room.id)
            row.runtime_location = {
                "device_id": "center",
                "resource_id": resource,
                "channel": "device",
            }
            token = bind_resource_token(
                mint_scoped_token(
                    project_id=str(case.project_id),
                    topic_id=str(task.id),
                    agent_handle=handle,
                ),
                resource,
                session_id=str(row.id),
            )
            await db.commit()
            return SimpleNamespace(
                room=task.id,
                channel=seat.room,
                session=row.id,
                token=token,
                task=task.id,
            )

    return run(case, open_line)


def mirror(*, running: bool = True, looked: datetime | None = None) -> dict:
    """A card's merge-state mirror as the poller writes one: every look rewrites
    the whole thing, ``checked_at`` being when it looked."""
    when = (looked or datetime.now(UTC)).isoformat()
    return {
        "state": "blocked" if running else "clean",
        "who": "agent",
        "reasons": (
            [
                {
                    "kind": "ci_running",
                    "checks": ["test"],
                    "detail": "CI 正在运行：test",
                }
            ]
            if running
            else []
        ),
        "head_sha": "0" * 40,
        "checked_at": when,
        "since": when,
    }


def delivery_is_in(
    case, line, *, state: dict | None, status=None, merged=False
) -> None:
    """The line handed a delivery over: its own card on that PR."""
    when = datetime.now(UTC) if merged else None

    async def add():
        async with case.client.test_request_factory() as db:
            db.add(
                AcceptCard(
                    topic_id=line.channel,
                    task_id=line.task,
                    reviewer_handle="alice",
                    status=status or AcceptStatus.pending,
                    pr_number=7,
                    pr_url="https://example.invalid/pr/7",
                    pr_merged_at=when,
                    merge_state=state,
                )
            )
            await db.commit()

    run(case, add)


def the_poller_s_next_look_says(case, line, state: dict) -> None:
    """What the poller writes on its next look at that card."""

    async def write():
        async with case.client.test_request_factory() as db:
            card = await db.scalar(
                select(AcceptCard).where(AcceptCard.task_id == line.task)
            )
            card.merge_state = state
            await db.commit()

    run(case, write)


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


def test_a_session_that_will_not_wait_is_answered_however_slow_the_new_sandbox(
    cloud, monkeypatch
):
    """A session that asks for its hands without waiting for them (a relaunch,
    or a tool asking first so that it can say the machine is being prepared)
    is answered within its own time, not the 50 s a waiting call may take,
    however long a step of making its new sandbox takes (dev, 2026-10-12: a
    relaunch held 50 s and then failed its start)."""
    monkeypatch.setattr(work_lease, "PREPARING_WAIT_S", 50.0)
    monkeypatch.setattr(work_lease, "ANSWER_S", 1.0, raising=False)
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")
    time_passes(cloud, seat, timedelta(minutes=11))
    assert sweep(cloud)["destroyed"] == 1

    async def slow(target, method, params, **kwargs):
        # The new sandbox's executor takes its time to come up.
        await asyncio.sleep(30)
        return await cloud.hosts.executor(target, method, params, **kwargs)

    monkeypatch.setattr(execution, "call", slow)
    asked = time.monotonic()
    answer = tool_call(cloud, seat, timeout=0.001)
    assert time.monotonic() - asked < 10
    assert answer.get("preparing"), answer
    assert "target" not in answer


def test_a_sandbox_is_kept_while_its_conversation_runs_a_turn_or_it_was_just_used(
    cloud,
):
    seat = cloud.seats[0]
    home = working_on(cloud, seat, "host-a")

    # Asked for a tool two minutes ago.
    time_passes(cloud, seat, timedelta(minutes=2))
    assert sweep(cloud)["destroyed"] == 0

    turn_runs(cloud, seat.room)
    # The turn has thought for an hour without asking for a tool.
    time_passes(cloud, seat, timedelta(hours=1))
    assert sweep(cloud)["destroyed"] == 0
    assert (home / "room" / "notes.md").exists()


def test_a_full_pool_takes_an_idle_cloud_sandbox_back_after_three_minutes(cloud):
    """池里没有空位时，空闲的云沙箱不再等到十分钟：三分钟就收，把槽让给下一个
    会话。不到三分钟仍算刚用过。"""
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")
    # The fixture's host runs one sandbox, and it is this one: no free slot.

    time_passes(cloud, seat, timedelta(minutes=2))
    assert sweep(cloud)["destroyed"] == 0
    assert home_of(cloud, seat) is not None

    time_passes(cloud, seat, timedelta(minutes=5))
    assert sweep(cloud)["destroyed"] == 1
    assert home_of(cloud, seat) is None


def test_with_a_free_slot_an_idle_cloud_sandbox_is_kept_until_ten_minutes(
    cloud, monkeypatch
):
    """池里还有空位时不提前收：空闲五分钟仍留着，到十分钟才走。"""
    monkeypatch.setattr(settings, "cloud_host_slots_per_core", 2)
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")

    time_passes(cloud, seat, timedelta(minutes=5))
    assert sweep(cloud)["destroyed"] == 0
    assert home_of(cloud, seat) is not None

    time_passes(cloud, seat, timedelta(minutes=11))
    assert sweep(cloud)["destroyed"] == 1


def test_a_session_waiting_on_its_sandbox_presses_the_pool(cloud, monkeypatch):
    """池里又有空槽、仍有会话在等自己的沙箱：等也是紧，空闲的照样按三分钟收。"""
    one, two, _ = cloud.seats
    working_on(cloud, one, "host-a")
    # Host A runs one sandbox and it is this one, so the second session is
    # placed on a host that is not up yet, and waits on it.
    assert tool_call(cloud, two).get("preparing")
    # The pool gains slots while that host is still dialling in: there is room
    # to place, and the wait alone is what presses the pool.
    monkeypatch.setattr(settings, "cloud_host_slots_per_core", 2)

    time_passes(cloud, one, timedelta(minutes=5))
    assert sweep(cloud)["destroyed"] == 1
    assert home_of(cloud, one) is None


def a_whole_vm_waits(cloud) -> None:
    """A session waiting on a whole cloud VM of its own: its host, and its home
    marked as waiting on it. Nothing else is placed on that machine, so freeing
    a slot of the shared pool does not bring it up."""

    async def seed():
        async with cloud.client.test_request_factory() as db:
            host = CloudHost(
                machine_id=999,
                customer_id=1,
                account_id=1,
                offering_id=1,
                hostname="vm-host",
                login_user="cheese",
                cores=4,
                memory_mb=8192,
                disk_gb=40,
                status=MachineStatus.running,
                whole_machine=True,
            )
            db.add(host)
            await db.flush()
            db.add(
                SandboxHome(
                    host_id=host.id,
                    spec="vm",
                    project_id=cloud.project_id,
                    resource_id=str(cloud.seats[2].room),
                    topic_id=cloud.seats[2].room,
                    room_resource_id=str(cloud.seats[2].room),
                    session_id=cloud.seats[2].session,
                    waiting_since=datetime.now(UTC),
                )
            )
            await db.commit()

    run(cloud, seed)


def test_a_whole_vms_wait_does_not_press_the_pool(cloud, monkeypatch):
    """等整机 VM 的会话不占用也没有释放的共享槽位：它等的时候，池里空闲的普通
    沙箱仍按十分钟保留，不被当成池子紧而提前收掉。"""
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")
    a_whole_vm_waits(cloud)
    # The shared host has a slot to give: the VM's wait alone is what a pressed
    # pool would otherwise be read from.
    monkeypatch.setattr(settings, "cloud_host_slots_per_core", 2)

    time_passes(cloud, seat, timedelta(minutes=5))
    assert sweep(cloud)["destroyed"] == 0
    assert home_of(cloud, seat) is not None


def test_a_background_command_past_the_cap_is_stopped_not_the_sandbox(cloud):
    """A command still running when the cap expires is stopped; the sandbox
    stays. The cap exists so a hung command cannot hold a host for good, and
    stopping the command does that without taking the home, the uncommitted
    work in it, and the place the waking session's next tool call lands."""
    seat = cloud.seats[0]
    home = working_on(cloud, seat, "host-a")
    cloud.hosts.background["host-a"] = 1

    time_passes(cloud, seat, timedelta(minutes=30))
    assert sweep(cloud)["destroyed"] == 0

    lifecycle._asked.clear()
    time_passes(cloud, seat, timedelta(minutes=61))
    assert sweep(cloud)["destroyed"] == 0
    assert cloud.hosts.signals == [("host-a", "task-0", lifecycle.TERM)]
    assert home_of(cloud, seat) is not None
    assert (home / "room" / "notes.md").exists()


def test_a_command_that_ends_before_the_sweep_keeps_its_sandbox(cloud, monkeypatch):
    """The end of a background command is the session waking to read it, not
    silence: the sweep that finds the command gone gives the home a fresh idle
    window instead of taking it in the seconds before the next tool call.

    Dev, 2026-10-21 23:08 (+08:00): a full test run of 15m05s outlived the
    15-minute cap by five seconds, and the sandbox was destroyed while the
    agent was already reading the result and asking for its first command.
    """
    seat = cloud.seats[0]
    home = working_on(cloud, seat, "host-a")
    # Every sweep asks: the just-ended command is recognised by the previous
    # answer, not by when the sweep happened to be allowed to ask.
    monkeypatch.setattr(lifecycle, "RECHECK", timedelta(0))
    cloud.hosts.background["host-a"] = 1

    time_passes(cloud, seat, timedelta(minutes=30))
    assert sweep(cloud)["destroyed"] == 0

    # The command ended between the two sweeps; the session is waking.
    cloud.hosts.background["host-a"] = 0
    time_passes(cloud, seat, timedelta(minutes=31))
    assert sweep(cloud)["destroyed"] == 0
    assert home_of(cloud, seat) is not None
    assert (home / "room" / "notes.md").exists()


def test_a_turn_that_starts_after_the_pick_keeps_the_sandbox(cloud):
    """The sweep picks a home from a look taken before the room's lock. A turn
    that starts in between — the session waking to a background command's
    result — is at work, and the destroy backs off when it takes the lock and
    sees it."""
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")
    time_passes(cloud, seat, timedelta(minutes=11))
    current = home_of(cloud, seat)
    assert current is not None
    turn_runs(cloud, seat.room)

    async def destroy():
        async with cloud.client.test_request_factory() as db:
            return await lifecycle.SandboxLifecycle(db, hub=cloud.hosts)._destroy(
                current.id,
                current.topic_id,
                "host-a",
                timedelta(minutes=11),
                idle_for=timedelta(minutes=10),
            )

    assert run(cloud, destroy) is False
    assert home_of(cloud, seat) is not None
    assert cloud.hosts.actions == []


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


def test_a_sandbox_its_agent_left_is_destroyed_and_holds_its_slot_until_then(
    cloud, monkeypatch
):
    """An agent that moves to another environment leaves a sandbox still
    running on its host. That sandbox is destroyed there once idle, as any
    other is, and the host counts it until then: placing another session in
    its slot meanwhile packed hosts past what they can run (dev, 2026-10: a
    host running 16 sandboxes where the pool counted 4, all of them short of
    memory)."""
    monkeypatch.setattr(settings, "microcloud_vm_offering_id", 1)
    one, two, three = cloud.seats
    home = working_on(cloud, one, "host-a")
    server = subprocess.Popen(["sleep", "600"], cwd=home / "room")
    try:
        moved = cloud.client.put(
            f"/topics/{one.room}/sessions/{one.session}/environment",
            headers=session_auth_headers("alice"),
            json={"environment": {"kind": "sandbox", "spec": "vm"}},
        )
        assert moved.status_code == 200, moved.text

        # Host A runs one sandbox at a time, and the one left still runs there.
        assert tool_call(cloud, two).get("preparing")
        assert host_of(cloud, two).id != host_of_machine_row(cloud, "host-a")
        assert home.exists()

        left_idle(cloud, home.name, timedelta(minutes=11))
        assert sweep(cloud)["destroyed"] == 1
        assert server.wait(timeout=30) is not None
    finally:
        server.kill()
    assert not home.exists()
    assert tool_call(cloud, three)["target"]["device_id"] == "host-a"


def left_idle(case, resource: str, idle: timedelta):
    """The sandbox in ``resource``, which its session left, was last used
    ``idle`` ago."""

    async def age():
        async with case.client.test_request_factory() as db:
            await db.execute(
                update(SandboxHome)
                .where(SandboxHome.resource_id == resource)
                .values(active_at=datetime.now(UTC) - idle)
            )
            await db.commit()

    run(case, age)


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
                update(SandboxHome)
                .where(SandboxHome.session_id == seat.session)
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


def test_a_line_waiting_on_its_checks_keeps_its_sandbox(cloud):
    """递上去等 CI 的线不是在收工走人：平台还替它看着那几张检查，等它们跑完
    就会叫它回来。这段等待里把机器收掉，它回来时得重开一台、把文档再读一遍。"""
    seat = cloud.seats[0]
    line = line_of(cloud, seat)
    home = working_on(cloud, line, "host-a")
    delivery_is_in(cloud, line, state=mirror())

    time_passes(cloud, line, timedelta(minutes=11))
    assert sweep(cloud)["destroyed"] == 0
    assert (home / "room" / "notes.md").exists()

    # 轮询下一次看它时检查已经绿了（卡还在等人采纳）：没有人在等 CI，机器照收。
    the_poller_s_next_look_says(cloud, line, mirror(running=False))
    assert sweep(cloud)["destroyed"] == 1
    assert home_of(cloud, line) is None


def test_a_line_waiting_on_its_checks_is_held_no_longer_than_the_cap(cloud):
    """等 CI 也不是无限期：过了后台命令那个上限，机器照样收 —— 一张没人再看
    第二眼的卡不能占着一台机器。"""
    seat = cloud.seats[0]
    line = line_of(cloud, seat)
    working_on(cloud, line, "host-a")
    delivery_is_in(cloud, line, state=mirror())

    time_passes(cloud, line, timedelta(minutes=30))
    assert sweep(cloud)["destroyed"] == 0

    time_passes(cloud, line, timedelta(minutes=61))
    # 轮询一直在看（镜像一直是新的），但这条线已经安静太久了。
    the_poller_s_next_look_says(cloud, line, mirror())
    assert sweep(cloud)["destroyed"] == 1


def test_a_checks_mirror_nobody_refreshes_does_not_keep_a_sandbox(cloud):
    """镜像停在「CI 还在跑」上，但早没人刷新它了 —— 轮询停了、卡撤了：那是
    上次看的样子，不是此刻在等。"""
    seat = cloud.seats[0]
    line = line_of(cloud, seat)
    working_on(cloud, line, "host-a")
    delivery_is_in(
        cloud, line, state=mirror(looked=datetime.now(UTC) - timedelta(minutes=20))
    )

    time_passes(cloud, line, timedelta(minutes=11))
    assert sweep(cloud)["destroyed"] == 1


def test_a_delivery_that_is_not_waiting_on_checks_does_not_keep_a_sandbox(cloud):
    """只有「一张没合并、还没作废的卡 + 刚看过 CI 在跑」才算在等。采纳过的卡
    （线在等人叫它写总结，不是等 CI）、已经合掉的卡、还没递出去的线，都照常收。"""
    one, two, three = cloud.seats
    lines = []
    for seat, device, title, fields in (
        (one, "host-a", "Landed", {"status": AcceptStatus.accepted}),
        (two, "host-b", "Merged and still pending", {"merged": True}),
        (three, "host-c", "Nothing sent", None),
    ):
        line = line_of(cloud, seat, title=title)
        working_on(cloud, line, device)
        if fields is not None:
            delivery_is_in(cloud, line, state=mirror(), **fields)
        lines.append(line)

    for line in lines:
        time_passes(cloud, line, timedelta(minutes=11))
    assert sweep(cloud)["destroyed"] == 3
