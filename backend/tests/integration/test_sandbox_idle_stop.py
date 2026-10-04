"""An idle cloud sandbox goes to sleep, and its session's next tool call wakes it.

A sandbox asleep keeps its home on the host's disk and no slot, so a host can
take new sessions while old ones sleep there. A home asleep for long is
archived to the private bucket and deleted from its host, and restored on
whichever host the session lands on next. A home is never deleted from a host
before its archive is verified.

The hosts here are directories on this machine: what the platform runs on a
host (``machine/sandbox_home.py``) runs here for real, with ``HOME`` pointed
at the host's directory. The bucket is a small HTTP server that stores what is
PUT to it and answers ETags the way S3 does.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
from app.domain.block.models import Block, BlockKind
from app.domain.identity.services import IdentityService
from app.domain.machine import lifecycle
from app.domain.machine import session_work as work_lease
from app.domain.machine.models import AiStatus, CloudHost, CloudHostHome, MachineStatus
from app.domain.machine.repositories import CloudHostRepository
from app.domain.machine.runner import SandboxSweeper
from app.domain.machine.services import HostPool
from app.domain.project.models import Project
from app.domain.team.models import Team
from app.domain.topic.models import Topic
from tests.integration.conftest import post_project, session_auth_headers
from tests.microcloud import FakeMicroCloud


class Bucket:
    """The private bucket: PUT, GET and HEAD of one object by a URL handed out
    for it. An object written in one PUT has its MD5 as ETag."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        # Store something other than what was sent, as a bucket that lost
        # bytes would.
        self.mangle = False
        bucket = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_PUT(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                bucket.objects[self.path.lstrip("/")] = (
                    body[:-1] if bucket.mangle else body
                )
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                body = bucket.objects.get(self.path.lstrip("/"))
                if body is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()

    async def presign(self, key, operation, expires_s):
        return f"http://127.0.0.1:{self._server.server_address[1]}/{key}"

    async def stat(self, key):
        body = self.objects.get(key)
        if body is None:
            return None
        return len(body), hashlib.md5(body).hexdigest()  # noqa: S324

    async def delete(self, key):
        return self.objects.pop(key, None) is not None


class Hosts:
    """The cloud hosts' connectors. A host program runs here, in the host's
    directory; an executor install is answered as one that started."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.online: set[str] = set()
        self.installs: list[str] = []
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
            done = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-"],
                input=stdin,
                text=True,
                capture_output=True,
                env={**os.environ, "HOME": str(self.home_dir(device_id))},
                timeout=timeout,
            )
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
        return {}


@pytest.fixture
def cloud(client, monkeypatch, tmp_path):
    """Three Cloud rooms of one project, each with one agent session. A host
    runs one sandbox at a time and keeps two homes on its disk."""
    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    monkeypatch.setattr(settings, "microcloud_default_cores", 1)
    monkeypatch.setattr(settings, "microcloud_default_disk_gb", 20)
    monkeypatch.setattr(settings, "cloud_host_slots_per_core", 1)
    monkeypatch.setattr(settings, "cloud_sandbox_disk_gb", 10)
    monkeypatch.setattr(settings, "cloud_pool_min_free_slots", 0)
    monkeypatch.setattr(settings, "cloud_host_idle_hold_s", 0)
    monkeypatch.setattr(settings, "cloud_sandbox_idle_stop_s", 600)
    monkeypatch.setattr(settings, "cloud_sandbox_background_cap_s", 3600)
    monkeypatch.setattr(settings, "cloud_sandbox_archive_after_s", 7 * 86400)
    provider = FakeMicroCloud()
    monkeypatch.setattr(
        "app.domain.machine.services.MicroCloudClient", lambda: provider
    )
    hosts = Hosts(tmp_path / "hosts")
    bucket = Bucket()
    monkeypatch.setattr(lifecycle, "private_storage", lambda: bucket)
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
        bucket=bucket,
        project_id=project_id,
        seats=seats,
    )
    bucket.close()


def run(case, coroutine_fn):
    return case.client.portal.call(coroutine_fn)


def tool_call(case, seat, timeout=5):
    """What a tool of the session asks first: its hands."""
    return case.client.post(
        f"/topics/{seat.room}/sessions/{seat.session}/work-lease",
        headers={"X-Cheese-Token": seat.token},
        json={"env": {}, "timeout": timeout},
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
            return (
                None
                if home is None or home.host_id is None
                else await repo.get(home.host_id)
            )

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
                    select(Block.content)
                    .where(
                        Block.topic_id == seat.room,
                        Block.kind == BlockKind.event,
                        Block.meta["event_type"]
                        .as_string()
                        .in_(["cloud_startup", "cloud_provisioning", "sandbox_asleep"]),
                    )
                    .order_by(Block.created_at)
                )
            )

    return run(case, read)


def test_an_idle_sandbox_sleeps_with_its_files_kept_and_wakes_on_the_next_tool(cloud):
    seat = cloud.seats[0]
    home = working_on(cloud, seat, "host-a")
    # A dev server the agent started and left running.
    server = subprocess.Popen(["sleep", "600"], cwd=home / "room")
    try:
        time_passes(cloud, seat, timedelta(minutes=11))
        assert sweep(cloud)["asleep"] == 1
        assert server.wait(timeout=30) is not None
    finally:
        server.kill()
    assert (home / "room" / "notes.md").read_text() == "not committed anywhere\n"
    assert home_of(cloud, seat).stopped_at is not None
    assert "沙箱 11 分钟没有活动，已休眠。文件都留着，下一条消息会唤醒它。" in (
        room_lines(cloud, seat)
    )

    installs = len(cloud.hosts.installs)
    answer = tool_call(cloud, seat)
    assert answer["target"]["device_id"] == "host-a"
    assert len(cloud.hosts.installs) == installs + 1
    assert home_of(cloud, seat).stopped_at is None
    assert room_lines(cloud, seat)[-2:] == ["正在唤醒沙箱", "沙箱已就绪"]
    assert (home / "room" / "notes.md").exists()


def test_a_sandbox_is_not_idle_while_its_room_runs_a_turn_or_it_was_just_used(cloud):
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")

    # Asked for a tool five minutes ago.
    time_passes(cloud, seat, timedelta(minutes=5))
    assert sweep(cloud)["asleep"] == 0

    async def turn_runs():
        async with cloud.client.test_request_factory() as db:
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

    run(cloud, turn_runs)
    # The turn has thought for an hour without asking for a tool.
    time_passes(cloud, seat, timedelta(hours=1))
    assert sweep(cloud)["asleep"] == 0
    assert home_of(cloud, seat).stopped_at is None


def test_a_background_command_keeps_a_sandbox_up_only_until_the_cap(cloud):
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")
    cloud.hosts.background["host-a"] = 1

    time_passes(cloud, seat, timedelta(minutes=30))
    assert sweep(cloud)["asleep"] == 0

    lifecycle._asked.clear()
    time_passes(cloud, seat, timedelta(minutes=61))
    assert sweep(cloud)["asleep"] == 1


def test_a_sleeping_sandbox_frees_its_slot_but_not_its_disk(cloud):
    one, two, three = cloud.seats
    working_on(cloud, one, "host-a")
    time_passes(cloud, one, timedelta(minutes=11))
    assert sweep(cloud)["asleep"] == 1

    # Host A runs one sandbox at a time: with the first asleep, the second
    # session is placed there.
    assert tool_call(cloud, two)["target"]["device_id"] == "host-a"
    time_passes(cloud, two, timedelta(minutes=11))
    assert sweep(cloud)["asleep"] == 1

    # Its disk keeps two homes, both taken: the third goes to another host.
    assert tool_call(cloud, three).get("preparing")
    assert host_of(cloud, three).id != host_of(cloud, one).id


def test_a_long_asleep_home_is_archived_and_restored_where_the_session_lands(cloud):
    seat = cloud.seats[0]
    home = working_on(cloud, seat, "host-a")
    (home / "room" / "notes.md").chmod(0o664)
    time_passes(cloud, seat, timedelta(minutes=11))
    assert sweep(cloud)["asleep"] == 1

    time_passes(cloud, seat, timedelta(days=8))
    assert sweep(cloud)["archived"] == 1
    archived = home_of(cloud, seat)
    assert archived.host_id is None
    assert list(cloud.bucket.objects) == [archived.archive_key]
    assert not home.exists()

    # With no home left on it, host A is released.
    machine = host_of_machine(cloud, "host-a")
    maintain(cloud)
    assert machine in cloud.provider.deleted

    assert tool_call(cloud, seat).get("preparing")
    host_comes_up(cloud, seat, "host-b")
    assert tool_call(cloud, seat)["target"]["device_id"] == "host-b"
    restored = sandbox_dir(cloud, seat, "host-b")
    assert (restored / "room" / "notes.md").read_text() == "not committed anywhere\n"
    assert (restored / "room" / "notes.md").stat().st_mode & 0o777 == 0o664
    assert home_of(cloud, seat).archive_key is None
    assert cloud.bucket.objects == {}
    assert "正在从归档恢复沙箱" in room_lines(cloud, seat)
    assert room_lines(cloud, seat)[-1] == "沙箱已就绪"


def test_a_home_whose_archive_does_not_verify_stays_on_its_host(cloud):
    seat = cloud.seats[0]
    home = working_on(cloud, seat, "host-a")
    time_passes(cloud, seat, timedelta(minutes=11))
    assert sweep(cloud)["asleep"] == 1

    cloud.bucket.mangle = True
    time_passes(cloud, seat, timedelta(days=8))
    assert sweep(cloud)["archived"] == 0
    kept = home_of(cloud, seat)
    assert kept.host_id == host_of(cloud, seat).id
    assert kept.archive_key is None
    assert (home / "room" / "notes.md").read_text() == "not committed anywhere\n"
    # Nothing half-written is left in the bucket either.
    assert cloud.bucket.objects == {}

    # A host whose sleeping home cannot be archived is not released.
    machine = host_of_machine(cloud, "host-a")
    maintain(cloud)
    maintain(cloud)
    assert machine not in cloud.provider.deleted


def test_a_sleeping_sandbox_on_a_full_host_moves_to_one_with_room(cloud):
    one, two, _ = cloud.seats
    working_on(cloud, one, "host-a")
    time_passes(cloud, one, timedelta(minutes=11))
    assert sweep(cloud)["asleep"] == 1
    # The second session takes host A's one slot.
    assert tool_call(cloud, two)["target"]["device_id"] == "host-a"

    # The first comes back: host A has no slot to wake it in.
    assert tool_call(cloud, one).get("preparing")
    assert room_lines(cloud, one)[-1] == "正在唤醒沙箱"
    assert sweep(cloud)["archived"] == 1
    assert home_of(cloud, one).host_id is None

    assert tool_call(cloud, one).get("preparing")
    host_comes_up(cloud, one, "host-c")
    assert tool_call(cloud, one)["target"]["device_id"] == "host-c"
    moved = sandbox_dir(cloud, one, "host-c")
    assert (moved / "room" / "notes.md").read_text() == "not committed anywhere\n"
    assert room_lines(cloud, one)[-1] == "沙箱已就绪"


def host_of_machine(case, device_id) -> int:
    async def read():
        async with case.client.test_request_factory() as db:
            return await db.scalar(
                select(CloudHost.machine_id).where(CloudHost.device_id == device_id)
            )

    return run(case, read)
