"""An idle cloud sandbox goes to sleep, and its session's next tool call wakes it.

A sandbox asleep keeps its home on the host's disk and no slot, so a host can
take new sessions while old ones sleep there. A home asleep for long is
archived to the private bucket and deleted from its host, and restored on
whichever host the session lands on next. A home is never deleted from a host
before its archive is verified.

The hosts here are directories on this machine: what the platform runs on a
host (``machine/sandbox_home.py``) runs here for real, with ``HOME`` pointed
at the host's directory. The bucket is a small HTTP server that speaks the
part of the S3 protocol an archive uses — multipart uploads through signed part
URLs, HEAD, GET, DELETE — and answers ETags the way S3 and R2 do; the platform
reaches it through its real S3 client.
"""

import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.core.storage import S3StorageBackend, multipart_etag
from app.domain.agent import execution
from app.domain.agent.compute_configs import ComputeChoice, standard_choice
from app.domain.agent.device_hub import device_hub
from app.domain.agent.models import AgentTurn
from app.domain.agent_session.services import AgentSessionService
from app.domain.identity.actor import Actor
from app.domain.identity.services import IdentityService
from app.domain.machine import lifecycle, sandbox_home
from app.domain.machine import session_work as work_lease
from app.domain.machine.models import AiStatus, CloudHost, CloudHostHome, MachineStatus
from app.domain.machine.repositories import CloudHostRepository
from app.domain.machine.runner import SandboxSweeper
from app.domain.machine.services import HostPool
from app.domain.project.models import Project
from app.domain.room_task.models import Task
from app.domain.run_record.models import RunRecord
from app.domain.team.models import Team
from app.domain.topic.models import Topic, TopicStatus
from app.domain.user.repositories import UserRepository
from tests.integration.conftest import post_project, session_auth_headers
from tests.microcloud import FakeMicroCloud


class Bucket:
    """The private bucket, spoken to over S3's protocol: ``storage`` is the
    platform's own client for it. ``objects`` holds what is stored by key;
    ``part_puts`` each part number a host sent, in order."""

    NAME = "private"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.etags: dict[str, str] = {}
        self.uploads: dict[str, dict] = {}
        self.part_puts: list[int] = []
        # Store something other than what was sent, as a bucket that lost
        # bytes would.
        self.mangle = False
        # Part numbers whose next PUT the bucket fails, as a dropped
        # connection or a 5xx does.
        self.fail_once: set[int] = set()
        self.lock = threading.Lock()
        bucket = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _target(self):
                parsed = urllib.parse.urlsplit(self.path)
                path = parsed.path.lstrip("/")
                assert path.split("/", 1)[0] == Bucket.NAME, path
                key = path.split("/", 1)[1] if "/" in path else ""
                return key, urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

            def _answer(self, status, body=b"", headers=()):
                self.send_response(status)
                for name, value in headers:
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _body(self):
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def do_PUT(self):
                key, query = self._target()
                body = self._body()
                number = int(query["partNumber"][0])
                with bucket.lock:
                    bucket.part_puts.append(number)
                    if number in bucket.fail_once:
                        bucket.fail_once.discard(number)
                        return self._answer(500)
                    upload = bucket.uploads.get(Bucket.upload_of(query))
                    if upload is None or upload["key"] != key:
                        return self._answer(404)
                    stored = body[:-1] if bucket.mangle else body
                    upload["parts"][number] = stored
                etag = hashlib.md5(stored).hexdigest()  # noqa: S324
                self._answer(200, headers=[("ETag", f'"{etag}"')])

            def do_POST(self):
                key, query = self._target()
                body = self._body()
                with bucket.lock:
                    if "uploads" in query:
                        upload_id = uuid.uuid4().hex
                        bucket.uploads[upload_id] = {"key": key, "parts": {}}
                        return self._answer(
                            200,
                            (
                                "<InitiateMultipartUploadResult>"
                                f"<Bucket>{Bucket.NAME}</Bucket><Key>{key}</Key>"
                                f"<UploadId>{upload_id}</UploadId>"
                                "</InitiateMultipartUploadResult>"
                            ).encode(),
                        )
                    upload = bucket.uploads.pop(Bucket.upload_of(query))
                    asked = [
                        int(n)
                        for n in re.findall(rb"<PartNumber>(\d+)</PartNumber>", body)
                    ]
                    parts = [upload["parts"][n] for n in asked]
                    # All parts but the last are the same size, as R2 requires.
                    assert len({len(p) for p in parts[:-1]}) <= 1
                    bucket.objects[key] = b"".join(parts)
                    etag = multipart_etag(
                        [hashlib.md5(p).hexdigest() for p in parts]  # noqa: S324
                    )
                    bucket.etags[key] = etag
                self._answer(
                    200,
                    (
                        "<CompleteMultipartUploadResult>"
                        f"<Key>{key}</Key><ETag>&quot;{etag}&quot;</ETag>"
                        "</CompleteMultipartUploadResult>"
                    ).encode(),
                )

            def do_GET(self):
                key, query = self._target()
                if "uploads" in query:
                    prefix = query.get("prefix", [""])[0]
                    with bucket.lock:
                        listed = "".join(
                            f"<Upload><Key>{u['key']}</Key>"
                            # Spelled anew on every listing, as R2 does.
                            f"<UploadId>{upload_id}.{uuid.uuid4().hex}</UploadId>"
                            "</Upload>"
                            for upload_id, u in bucket.uploads.items()
                            if u["key"].startswith(prefix)
                        )
                    return self._answer(
                        200,
                        (
                            "<ListMultipartUploadsResult>"
                            f"<Bucket>{Bucket.NAME}</Bucket>{listed}"
                            "</ListMultipartUploadsResult>"
                        ).encode(),
                    )
                body = bucket.objects.get(key)
                if body is None:
                    return self._answer(404)
                self._answer(200, body)

            def do_HEAD(self):
                key, _ = self._target()
                body = bucket.objects.get(key)
                if body is None:
                    return self._answer(404)
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("ETag", f'"{bucket.etags[key]}"')
                self.end_headers()

            def do_DELETE(self):
                key, query = self._target()
                with bucket.lock:
                    if "uploadId" in query:
                        bucket.uploads.pop(Bucket.upload_of(query), None)
                    else:
                        bucket.objects.pop(key, None)
                        bucket.etags.pop(key, None)
                self._answer(204)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.storage = S3StorageBackend(
            bucket=Bucket.NAME,
            endpoint_url=f"http://127.0.0.1:{self._server.server_address[1]}",
            access_key="test",
            secret_key="test",
        )

    @staticmethod
    def upload_of(query) -> str:
        """The upload an id names, however it is spelled."""
        return query["uploadId"][0].split(".")[0]

    def close(self) -> None:
        self._server.shutdown()


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
    monkeypatch.setattr(lifecycle, "private_storage", lambda: bucket.storage)
    # Parts small enough that a test's home takes several.
    monkeypatch.setattr(lifecycle, "PART_SIZE", 64 * 1024)
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
    assert "环境 11 分钟没有活动，已休眠。文件都留着，下一条消息会唤醒它。" in (
        room_lines(cloud, seat)
    )

    installs = len(cloud.hosts.installs)
    answer = tool_call(cloud, seat)
    assert answer["target"]["device_id"] == "host-a"
    assert len(cloud.hosts.installs) == installs + 1
    assert home_of(cloud, seat).stopped_at is None
    assert room_lines(cloud, seat)[-2:] == ["正在唤醒环境", "环境已就绪"]
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
                    conversation_id=seat.room,
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
    assert "正在从归档恢复环境" in room_lines(cloud, seat)
    assert room_lines(cloud, seat)[-1] == "环境已就绪"


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


def a_large_home(case, seat) -> Path:
    """A home whose archive is several parts: what the session left in it
    does not compress."""
    home = working_on(case, seat, "host-a")
    (home / "room" / "build.bin").write_bytes(os.urandom(300 * 1024))
    return home


def sent_once_each(case) -> bool:
    return sorted(case.bucket.part_puts) == sorted(set(case.bucket.part_puts))


def test_a_restart_mid_archive_does_not_send_the_home_again(cloud):
    """On dev a 1.3 GB home took longer to write and send than the platform
    ran between deploys: each deploy stopped the archive, which counted as a
    failed one and was not tried for six hours, while the host finished the
    upload to an object nobody recorded (2026-10-07). The host goes on without
    the platform, and the next sweep takes up where it got to."""
    seat = cloud.seats[0]
    home = a_large_home(cloud, seat)
    build = (home / "room" / "build.bin").read_bytes()
    asleep(cloud, seat)
    time_passes(cloud, seat, timedelta(days=8))

    async def platform_stops(_device_id, action):
        if action == "archive":
            cloud.hosts.after = None
            raise asyncio.CancelledError

    cloud.hosts.after = platform_stops

    async def deploy_interrupts():
        try:
            await SandboxSweeper(cloud.client.test_request_factory).sweep()
        except asyncio.CancelledError:
            return
        raise AssertionError("the sweep was not interrupted")

    run(cloud, deploy_interrupts)
    interrupted = home_of(cloud, seat)
    assert interrupted.archive_failed_at is None
    assert interrupted.busy_until is None
    sent = len(cloud.bucket.part_puts)
    assert sent > 1

    assert sweep(cloud)["archived"] == 1
    assert len(cloud.bucket.part_puts) == sent
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
    assert (restored / "room" / "build.bin").read_bytes() == build


def test_an_archive_longer_than_a_sweep_waits_is_finished_by_a_later_one(
    cloud, monkeypatch
):
    """The later sweep finds the upload by listing the bucket's, and R2 spells
    one upload's id differently on every listing. On dev each sweep took that
    for a new upload, stopped the host's job and threw its parts away, until a
    stop that raced another call failed the archive (2026-10-07)."""
    seat = cloud.seats[0]
    a_large_home(cloud, seat)
    asleep(cloud, seat)
    time_passes(cloud, seat, timedelta(days=8))
    monkeypatch.setattr(lifecycle, "ARCHIVE_WAIT_S", 0)

    assert sweep(cloud)["archived"] == 0
    waiting = home_of(cloud, seat)
    assert waiting.host_id is not None
    assert waiting.archive_failed_at is None and waiting.busy_until is None

    monkeypatch.setattr(lifecycle, "ARCHIVE_WAIT_S", 30)
    assert sweep(cloud)["archived"] == 1
    assert home_of(cloud, seat).host_id is None
    assert sent_once_each(cloud)


def test_a_part_the_bucket_drops_is_sent_again_alone(cloud):
    seat = cloud.seats[0]
    a_large_home(cloud, seat)
    asleep(cloud, seat)
    time_passes(cloud, seat, timedelta(days=8))
    cloud.bucket.fail_once = {2}

    assert sweep(cloud)["archived"] == 1
    puts = cloud.bucket.part_puts
    assert puts.count(2) == 2
    assert all(puts.count(n) == 1 for n in set(puts) - {2})
    assert home_of(cloud, seat).host_id is None


def test_a_sleeping_sandbox_on_a_full_host_moves_to_one_with_room(cloud):
    one, two, _ = cloud.seats
    working_on(cloud, one, "host-a")
    time_passes(cloud, one, timedelta(minutes=11))
    assert sweep(cloud)["asleep"] == 1
    # The second session takes host A's one slot.
    assert tool_call(cloud, two)["target"]["device_id"] == "host-a"

    # The first comes back: host A has no slot to wake it in.
    assert tool_call(cloud, one).get("preparing")
    assert room_lines(cloud, one)[-1] == "正在唤醒环境"
    # Host A is up: the pool's sweep must not take that for the sandbox.
    maintain(cloud)
    assert room_lines(cloud, one)[-1] == "正在唤醒环境"
    assert sweep(cloud)["archived"] == 1
    assert home_of(cloud, one).host_id is None

    assert tool_call(cloud, one).get("preparing")
    host_comes_up(cloud, one, "host-c")
    assert tool_call(cloud, one)["target"]["device_id"] == "host-c"
    moved = sandbox_dir(cloud, one, "host-c")
    assert (moved / "room" / "notes.md").read_text() == "not committed anywhere\n"
    assert room_lines(cloud, one)[-1] == "环境已就绪"


def host_of_machine(case, device_id) -> int:
    async def read():
        async with case.client.test_request_factory() as db:
            return await db.scalar(
                select(CloudHost.machine_id).where(CloudHost.device_id == device_id)
            )

    return run(case, read)


# --- found in review: each of these lost or stranded a session's work --------


def asleep(case, seat):
    time_passes(case, seat, timedelta(minutes=11))
    assert sweep(case)["asleep"] == 1


def archived(case, seat) -> str:
    """The home goes to the bucket, and its host, empty, is released; returns
    the archive's key."""
    time_passes(case, seat, timedelta(days=8))
    assert sweep(case)["archived"] == 1
    maintain(case)
    home = home_of(case, seat)
    assert home.host_id is None
    return home.archive_key


def switch_away(case, seat):
    """A person points the room at a self-hosted machine."""

    async def go():
        async with case.client.test_request_factory() as db:
            user = await UserRepository(db).get_by_handle("alice")
            await work_lease.request_choice(
                db,
                topic_id=seat.room,
                actor=Actor("alice", user.id, "token"),
                choice=ComputeChoice(profile="device", name="Laptop", device_id="lap"),
            )

    run(case, go)


def left_home(case, seat) -> CloudHostHome:
    async def read():
        async with case.client.test_request_factory() as db:
            return await db.scalar(
                select(CloudHostHome).where(CloudHostHome.session_id == seat.session)
            )

    return run(case, read)


def test_a_switch_before_the_archive_is_restored_keeps_the_archive(cloud):
    """Placed on a new host that is not up yet, the session's work is still
    only in its archive. Its old host is up and would answer a push, of a home
    that is no longer there."""
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")
    asleep(cloud, seat)
    key = archived(cloud, seat)
    assert tool_call(cloud, seat).get("preparing")
    assert home_of(cloud, seat).host_id is not None

    switch_away(cloud, seat)

    assert key in cloud.bucket.objects
    kept = left_home(cloud, seat)
    assert kept.left_at is not None and kept.archive_key == key


def test_a_home_that_cannot_be_archived_is_not_tried_on_every_sweep(cloud):
    seat = cloud.seats[0]
    home = working_on(cloud, seat, "host-a")
    secret = home / "room" / "unreadable"
    secret.write_text("x")
    secret.chmod(0)
    try:
        asleep(cloud, seat)
        time_passes(cloud, seat, timedelta(days=8))
        assert sweep(cloud)["archived"] == 0
        assert sweep(cloud)["archived"] == 0
    finally:
        secret.chmod(0o600)
    assert [a for _, a in cloud.hosts.actions if a == "archive"] == ["archive"]
    kept = home_of(cloud, seat)
    assert kept.host_id is not None and kept.archive_error
    assert (home / "room" / "notes.md").exists()
    assert cloud.bucket.objects == {}


def test_an_unreadable_core_dump_does_not_stop_a_home_being_archived(cloud, caplog):
    """A process that crashed in the sandbox left a core dump the host cannot
    read. On dev one such file failed a home's archive every six hours, and
    kept its host (2026-10-05). The dump is left out, and the log names it; the
    work around it is archived and the host is let go."""
    seat = cloud.seats[0]
    home = working_on(cloud, seat, "host-a")
    dump = home / "room" / "frontend" / "core.1"
    dump.parent.mkdir(parents=True, exist_ok=True)
    dump.write_bytes(b"\x7fELF")
    dump.chmod(0)
    try:
        asleep(cloud, seat)
        time_passes(cloud, seat, timedelta(days=8))
        with caplog.at_level("WARNING", logger="cheese.machine.lifecycle"):
            assert sweep(cloud)["archived"] == 1
    finally:
        if dump.exists():
            dump.chmod(0o600)
    assert any(
        "room/frontend/core.1" in record.getMessage() for record in caplog.records
    )

    machine = host_of_machine(cloud, "host-a")
    maintain(cloud)
    assert machine in cloud.provider.deleted
    assert tool_call(cloud, seat).get("preparing")
    host_comes_up(cloud, seat, "host-b")
    assert tool_call(cloud, seat)["target"]["device_id"] == "host-b"
    restored = sandbox_dir(cloud, seat, "host-b")
    assert (restored / "room" / "notes.md").read_text() == "not committed anywhere\n"
    assert not (restored / "room" / "frontend" / "core.1").exists()


def test_a_sleeping_home_no_longer_on_its_host_lets_the_host_go(cloud):
    """Its directory was removed from the host, so there is nothing to archive
    and nothing for the host to keep: the host is released, and the session's
    next tool call gets a sandbox like a new one. On dev, hosts adopted from
    before the pool were kept for days by homes like this (2026-10-06)."""
    seat = cloud.seats[0]
    home = working_on(cloud, seat, "host-a")
    asleep(cloud, seat)
    shutil.rmtree(home)

    time_passes(cloud, seat, timedelta(days=8))
    sweep(cloud)
    machine = host_of_machine(cloud, "host-a")
    maintain(cloud)

    assert machine in cloud.provider.deleted
    assert cloud.bucket.objects == {}
    assert tool_call(cloud, seat).get("preparing")
    host_comes_up(cloud, seat, "host-b")
    assert tool_call(cloud, seat)["target"]["device_id"] == "host-b"


def test_an_archive_stops_as_soon_as_it_passes_the_limit():
    scope = {"__name__": "sandbox_home"}
    exec(Path(sandbox_home.__file__).read_text(), scope)
    sink = io.BytesIO()
    counted = scope["_Counted"](sink, limit=10)
    counted.write(b"12345")
    with pytest.raises(scope["TooLarge"]):
        counted.write(b"123456")
    assert sink.getvalue() == b"12345"


def test_a_home_not_yet_restored_is_never_archived_over_its_archive(cloud):
    """Placed on a new host and left there half restored, then idle: its work
    is the archive it already has, not what is on that host."""
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")
    asleep(cloud, seat)
    key = archived(cloud, seat)
    assert tool_call(cloud, seat).get("preparing")
    host_comes_up(cloud, seat, "host-b")
    half = sandbox_dir(cloud, seat, "host-b")
    (half / "room").mkdir(parents=True)
    (half / "room" / "half.txt").write_text("half")

    time_passes(cloud, seat, timedelta(minutes=11))
    sweep(cloud)

    home = home_of(cloud, seat)
    assert home.archive_key == key and home.host_id is None
    assert list(cloud.bucket.objects) == [key]
    assert not half.exists()


def test_a_session_on_a_gone_host_before_its_restore_is_placed_again(cloud):
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")
    asleep(cloud, seat)
    key = archived(cloud, seat)
    assert tool_call(cloud, seat).get("preparing")
    gone = home_of(cloud, seat).host_id

    async def provider_lost_it():
        async with cloud.client.test_request_factory() as db:
            host = await db.get(CloudHost, gone)
            cloud.provider.machines.pop(host.machine_id)
            host.status = MachineStatus.deleted
            await db.commit()

    run(cloud, provider_lost_it)

    assert tool_call(cloud, seat).get("preparing")
    home = home_of(cloud, seat)
    assert home.archive_key == key and home.host_id not in (None, gone)


def test_a_failed_restore_with_read_only_leftovers_does_not_block_the_next(cloud):
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")
    asleep(cloud, seat)
    archived(cloud, seat)
    assert tool_call(cloud, seat).get("preparing")
    host_comes_up(cloud, seat, "host-b")
    resource = home_of(cloud, seat).resource_id
    # What a restore that died midway leaves: a Go module cache, read-only.
    left = cloud.hosts.home_dir("host-b") / ".cheese/archives" / f"{resource}.restore"
    (left / "home/go/pkg/mod").mkdir(parents=True)
    (left / "home/go/pkg/mod/x.go").write_text("package x")
    (left / "home/go/pkg/mod").chmod(0o555)

    assert tool_call(cloud, seat)["target"]["device_id"] == "host-b"
    restored = sandbox_dir(cloud, seat, "host-b")
    assert (restored / "room" / "notes.md").read_text() == "not committed anywhere\n"


def test_a_room_mid_turn_does_not_keep_other_sandboxes_awake(cloud, monkeypatch):
    monkeypatch.setattr(lifecycle, "STOPS_PER_SWEEP", 1)
    busy, quiet, _ = cloud.seats
    working_on(cloud, busy, "host-a")
    working_on(cloud, quiet, "host-b")

    async def turn_runs():
        async with cloud.client.test_request_factory() as db:
            db.add(
                AgentTurn(
                    id=uuid.uuid4(),
                    conversation_id=busy.room,
                    continuation_id=uuid.uuid4(),
                    author="alice",
                    started_at=datetime.now(UTC),
                )
            )
            await db.commit()

    run(cloud, turn_runs)
    time_passes(cloud, busy, timedelta(hours=1))
    time_passes(cloud, quiet, timedelta(minutes=11))

    assert sweep(cloud)["asleep"] == 1
    assert home_of(cloud, quiet).stopped_at is not None


def test_a_task_at_work_in_the_room_does_not_keep_another_sessions_sandbox_awake(
    cloud,
):
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

    assert sweep(cloud)["asleep"] == 1
    assert home_of(cloud, seat).stopped_at is not None


def test_a_home_on_an_offline_host_does_not_hold_up_other_archives(cloud, monkeypatch):
    monkeypatch.setattr(lifecycle, "ARCHIVES_PER_SWEEP", 1)
    offline, online, _ = cloud.seats
    working_on(cloud, offline, "host-a")
    working_on(cloud, online, "host-b")
    asleep(cloud, offline)
    asleep(cloud, online)
    time_passes(cloud, offline, timedelta(days=9))
    time_passes(cloud, online, timedelta(days=8))
    cloud.hosts.online.discard("host-a")

    assert sweep(cloud)["archived"] == 1
    assert home_of(cloud, online).host_id is None


def test_the_interpreter_a_venv_links_to_comes_back_with_the_home(cloud):
    """uv installs Python into the project's package store, outside the home;
    a venv restored on another host links to it there."""
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")
    python = (
        cloud.hosts.home_dir("host-a")
        / ".cheese/store"
        / str(cloud.project_id)
        / "uv-python/cpython-3.12.9-linux-x86_64-gnu/bin/python3.12"
    )
    python.parent.mkdir(parents=True)
    python.write_text("#!interpreter")
    asleep(cloud, seat)
    archived(cloud, seat)

    assert tool_call(cloud, seat).get("preparing")
    host_comes_up(cloud, seat, "host-b")
    assert tool_call(cloud, seat)["target"]["device_id"] == "host-b"
    carried = Path(
        str(python).replace(
            str(cloud.hosts.home_dir("host-a")), str(cloud.hosts.home_dir("host-b"))
        )
    )
    assert carried.read_text() == "#!interpreter"


def test_a_restore_never_follows_a_link_the_session_left_in_its_home(cloud):
    """The archive is the session's tree. A link where the platform's own
    directory goes would carry the restore's deletions outside the home."""
    seat = cloud.seats[0]
    home = working_on(cloud, seat, "host-a")
    victim = cloud.hosts.home_dir("host-b") / "another-room" / "executor"
    victim.mkdir(parents=True)
    (victim / "state").write_text("another room's executor")
    (home / ".cheese").symlink_to(victim.parent)
    asleep(cloud, seat)
    archived(cloud, seat)

    assert tool_call(cloud, seat).get("preparing")
    host_comes_up(cloud, seat, "host-b")
    answer = tool_call(cloud, seat)

    assert answer.get("unavailable") == work_lease.SANDBOX_RESTORE_FAILED
    assert (victim / "state").read_text() == "another room's executor"


def test_a_restore_holds_the_home_until_it_is_done(cloud):
    """A tool call's claim on its session lapses long before a large restore
    ends; the home itself is held, so a second call waits instead of
    restoring over the first."""
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")
    asleep(cloud, seat)
    archived(cloud, seat)
    assert tool_call(cloud, seat).get("preparing")
    host_comes_up(cloud, seat, "host-b")
    held = []

    async def look(device_id, action):
        if action == "restore":
            async with cloud.client.test_request_factory() as db:
                busy = await db.scalar(
                    select(CloudHostHome.busy_until).where(
                        CloudHostHome.session_id == seat.session
                    )
                )
                held.append(busy is not None and busy > datetime.now(UTC))

    cloud.hosts.before = look
    assert tool_call(cloud, seat)["target"]["device_id"] == "host-b"
    assert held == [True]
    assert home_of(cloud, seat).busy_until is None


def test_an_archived_rooms_sandbox_sleeps_and_frees_its_host(cloud):
    """An archived room whose cleanup has not finished still had its sandbox
    running, and stopping it raised: every sweep failed there, no sandbox of
    any room slept, and no host was ever released (dev, 2026-10-05)."""
    archived_room, other, _ = cloud.seats
    working_on(cloud, archived_room, "host-a")
    working_on(cloud, other, "host-b")

    async def archive():
        async with cloud.client.test_request_factory() as db:
            (await db.get(Topic, archived_room.room)).status = TopicStatus.archived
            await db.commit()

    run(cloud, archive)
    time_passes(cloud, archived_room, timedelta(minutes=11))
    time_passes(cloud, other, timedelta(minutes=11))

    assert sweep(cloud)["asleep"] == 2
    assert home_of(cloud, archived_room).stopped_at is not None
    assert home_of(cloud, other).stopped_at is not None

    # Asleep long enough, the home goes to the bucket and its host is
    # released, while the room's own cleanup is still to come.
    machine = host_of_machine(cloud, "host-a")
    time_passes(cloud, archived_room, timedelta(days=8))
    assert sweep(cloud)["archived"] == 1
    maintain(cloud)
    assert home_of(cloud, archived_room).host_id is None
    assert machine in cloud.provider.deleted


def room_cleanup_after_its_home_left(case, seat, monkeypatch) -> dict:
    """The room is archived and its cleanup is due. The home went to the
    bucket and its host was released before the cleanup reached it, but the
    cleanup recorded the home on that host when it took its inventory — the
    rooms PR #2749 left waiting on "device … is offline". Returns what one
    sweep of room cleanups answered."""
    from app.domain.topic import retire
    from app.domain.topic.services import TopicService

    resource = home_of(case, seat).resource_id
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
        case, lambda: retire.sweep_retired_storage(case.client.test_request_factory)
    )


def cleanup_of(case, seat) -> dict:
    return case.client.get(
        f"/topics/{seat.room}/cleanup", headers=session_auth_headers("alice")
    ).json()["data"]


def test_an_archived_rooms_unpushed_work_waits_in_the_bucket_and_comes_back(
    cloud, monkeypatch
):
    """FB-68: an archived room's unpushed work kept its cleanup retrying and
    its cloud machines held for 31 hours. The home now leaves its host for
    the bucket, and the host is released; the cleanup must neither delete
    that archive — it is the only copy — nor wait on the gone host. It waits
    on the work, says so, and unarchiving the room brings the work back."""
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")
    asleep(cloud, seat)
    key = archived(cloud, seat)
    cloud.hosts.online.discard("host-a")

    assert room_cleanup_after_its_home_left(cloud, seat, monkeypatch) == {
        "completed": 0,
        "pending": 1,
    }
    status = cleanup_of(cloud, seat)
    assert status["state"] == "pending"
    assert "not pushed" in status["reason"]
    assert key in cloud.bucket.objects

    response = cloud.client.post(
        f"/topics/{seat.room}/unarchive", headers=session_auth_headers("alice")
    )
    assert response.status_code == 200, response.text
    assert tool_call(cloud, seat).get("preparing")
    host_comes_up(cloud, seat, "host-b")
    assert tool_call(cloud, seat)["target"]["device_id"] == "host-b"
    restored = sandbox_dir(cloud, seat, "host-b")
    assert (restored / "room" / "notes.md").read_text() == "not committed anywhere\n"


def test_an_archived_rooms_pushed_home_in_the_bucket_lets_its_cleanup_finish(
    cloud, monkeypatch
):
    """The same room with everything pushed: the cleanup does not wait on the
    released host, finishes, and the archive goes with it."""
    seat = cloud.seats[0]
    home = working_on(cloud, seat, "host-a")
    (home / "room" / "notes.md").unlink()
    asleep(cloud, seat)
    key = archived(cloud, seat)
    assert key in cloud.bucket.objects
    cloud.hosts.online.discard("host-a")

    assert room_cleanup_after_its_home_left(cloud, seat, monkeypatch) == {
        "completed": 1,
        "pending": 0,
    }
    assert cleanup_of(cloud, seat)["state"] in {"complete", "retained"}
    assert cloud.bucket.objects == {}
