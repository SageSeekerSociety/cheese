"""Archive takes a place's disk with it, and the sweep takes what archive missed.

A room's git worktree on this box and its isolated home on the device that ran
it used to outlive the room forever (dev box, 2026-09-03: 141 GB of worktrees
and 162 GB of homes, most of them for places long gone). These tests drive the
real `git worktree` layout under a temporary workspace root and a fake connector
attached to the real ``DeviceHub`` — the device end of the link, answering
``exec`` frames the way the Go connector does — so what is asserted is what a
box and a device actually end up with, not which helper was called.

A home is stored before it goes. The fake device answers the upload command
the way the shell would — with a real tar.gz PUT at the platform's own route,
built from what its home holds — so what is asserted is the archive that
landed under `transcripts_dir` and the stamp on the place, and that a home
whose upload did not go through is still there.
"""

import hashlib
import io
import os
import re
import subprocess
import tarfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy import update

from app.core.config import settings
from app.domain.agent.device_hub import device_hub
from app.domain.device.models import DeviceRow, HostedDeviceRow
from app.domain.device.supply import Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.project.services import ProjectService
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.services import TaskService, WorkTreeService
from app.domain.topic.models import Topic, TopicKind, TopicStatus
from app.domain.topic.retire import sweep_retired_storage
from app.domain.topic.services import TopicService
from app.domain.workspace import service as ws
from app.main import app as asgi_app

pytestmark = pytest.mark.anyio


class FakeConnector:
    """A device's end of the link: records every command the platform runs on
    it and answers as a shell would — a listing of the homes it holds, the
    transcript upload (a real PUT at the platform, from what the home holds),
    and ``rm -rf`` removing one of them."""

    def __init__(self, device_id: str, homes: list[str] | None = None) -> None:
        self.device_id = device_id
        self.homes = list(homes or [])  # "<project>/<place>" pairs on disk
        # The one session line a home's `.claude/projects` holds. A home absent
        # here never ran a session and has no such directory at all.
        self.sessions: dict[str, str] = {}
        self.removed: list[str] = []  # home paths an rm reached
        self.uploads: list[str] = []  # homes whose archive the platform took
        self.reachable = True  # False: curl cannot reach the platform at all

    async def send_json(self, msg: dict) -> None:
        if msg.get("t") != "exec":
            return
        script = msg["command"][-1]
        stdout, stderr, exit_code = "", "", 0
        if "for p in */*" in script:
            stdout = "".join(f"{h}\n" for h in self.homes)
        elif "/transcripts/" in script:
            stdout, stderr, exit_code = await self._upload(script, msg.get("env") or {})
        elif script.startswith("rm -rf"):
            path = script.split('"')[1]
            self.removed.append(path)
            self.homes = [h for h in self.homes if not path.endswith(h)]
        # The result frame the connector sends back; the hub resolves the
        # pending exec future from it.
        await device_hub.on_device_message(
            self.device_id,
            {
                "t": "exec.result",
                "id": msg["id"],
                "stdout": stdout,
                "stderr": stderr,
                "exit": exit_code,
                "truncated": False,
            },
        )

    async def _upload(self, script: str, env: dict[str, str]) -> tuple[str, str, int]:
        """What the upload script comes to on this device: nothing to send,
        no way to send it, or a PUT and curl's verdict on the answer."""
        home = script.split('"')[1]
        key = "/".join(home.split("/")[-2:])
        line = self.sessions.get(key) if key in self.homes else None
        if line is None:
            return "none\n", "", 0
        if not self.reachable:
            return "", "curl: (7) Failed to connect to the platform", 7
        # The URL the shell would build: the base from its env, the path from
        # the script.
        match = re.search(r'\$CHEESE_API(/transcripts/[^"]+)"', script)
        assert match is not None, script
        path = urlsplit(env["CHEESE_API"]).path + match.group(1)
        response = await _put(path, _home_archive(line), token=env["CHEESE_TOKEN"])
        if not response.is_success:
            reason = (
                f"curl: (22) The requested URL returned error: {response.status_code}"
            )
            return "", reason, 22
        self.uploads.append(key)
        return response.text + "\nuploaded\n", "", 0


async def _put(path: str, body: bytes, *, token: str | None) -> httpx.Response:
    """A request at the platform's own route, from inside the test's loop."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=asgi_app), base_url="http://device"
    ) as http:
        return await http.put(path, content=body, headers=headers)


def _home_archive(line: str) -> bytes:
    """A home's `.claude/projects` and `.claude/todos`, packed the way the
    upload script's tar packs them."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in (
            (".claude/projects/-home-agent-work/session.jsonl", line),
            (".claude/todos/agent.json", "[]"),
        ):
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _stored_archives(project_id: uuid.UUID, place_id: uuid.UUID) -> list[Path]:
    place = Path(settings.transcripts_dir) / str(project_id) / str(place_id)
    return sorted(place.glob("*.tar.gz"))


def _session_lines(archive: Path) -> list[str]:
    with tarfile.open(archive, "r:gz") as tar:
        return [
            (tar.extractfile(member) or io.BytesIO()).read().decode()
            for member in tar
            if member.isfile() and member.name.endswith(".jsonl")
        ]


@pytest.fixture
def workspace_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "ws"
    monkeypatch.setattr(settings, "workspace_root", str(root))
    return root


@pytest.fixture(autouse=True)
def transcripts_dir(tmp_path, monkeypatch) -> Path:
    """Where uploads land; every test here may store one."""
    root = tmp_path / "transcripts"
    monkeypatch.setattr(settings, "transcripts_dir", str(root))
    return root


@pytest.fixture
async def connected_device():
    """A fake device attached to the process-wide hub for one test, and gone
    again after it — other tests read ``online_device_ids`` too."""
    fake = FakeConnector("dev-retire")
    await device_hub.attach_device(fake.device_id, fake)
    try:
        yield fake
    finally:
        await device_hub.detach_device(fake.device_id, fake)


async def _seed_device(
    session, device_id: str, *, project_id: uuid.UUID | None = None
) -> None:
    """An enrolled self-hosted machine, optionally one the project may run on."""
    owner = await IdentityService(session).ensure_agent_user()
    session.add(
        DeviceRow(
            device_id=device_id,
            name=device_id,
            token=f"tok-{device_id}",
            owner_user_id=owner.id,
            created_at=datetime.now(UTC),
        )
    )
    session.add(HostedDeviceRow(device_id=device_id, owner_user_id=owner.id))
    await session.flush()
    if project_id is not None:
        await sql_device_service(session).assign_to_project(
            device_id, project_id, actor_user_id=owner.id
        )


def _registered_worktrees(project_id: uuid.UUID) -> str:
    """What git itself still lists for the project's repo."""
    return subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=ws.ensure_repo(project_id),
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _home(project_id: uuid.UUID, place_id: uuid.UUID) -> str:
    return f"{project_id}/{place_id}"


async def test_archive_removes_the_worktree_and_the_device_home(
    client, workspace_root, connected_device
):
    factory = client.test_factory
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="退掉", created_by="u"
        )
        await _seed_device(session, connected_device.device_id)
        await sql_device_service(session).bind_topic_device(
            topic.id, connected_device.device_id, Visibility.host
        )
        await session.commit()
        pid, tid = project.id, topic.id

    wt = ws._ensure_worktree(pid, tid)  # noqa: SLF001 — the real layout
    assert wt.is_dir() and str(wt) in _registered_worktrees(pid)
    connected_device.homes = [_home(pid, tid)]

    async with factory() as session:
        archived = await TopicService(session).archive(tid, by="u")
        await session.commit()

    assert archived.status == TopicStatus.archived
    assert not wt.exists()
    assert str(wt) not in _registered_worktrees(pid)
    assert connected_device.removed == [f"$HOME/.cheese/home/{pid}/{tid}"]
    assert connected_device.homes == []
    # Nothing ever ran in that home, so there was nothing to keep: no upload,
    # nothing on disk here, no stamp.
    assert connected_device.uploads == []
    assert _stored_archives(pid, tid) == []
    async with factory() as session:
        topic = await TopicService(session).get_or_404(tid)
        assert topic.transcripts_archived_at is None


async def test_archive_stores_the_transcripts_before_removing_the_home(
    client, workspace_root, connected_device
):
    factory = client.test_factory
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="留底", created_by="u"
        )
        await _seed_device(session, connected_device.device_id)
        await sql_device_service(session).bind_topic_device(
            topic.id, connected_device.device_id, Visibility.host
        )
        await session.commit()
        pid, tid = project.id, topic.id
    connected_device.homes = [_home(pid, tid)]
    connected_device.sessions[_home(pid, tid)] = '{"type":"user","text":"把它归档"}'

    async with factory() as session:
        await TopicService(session).archive(tid, by="u")
        await session.commit()

    assert connected_device.uploads == [_home(pid, tid)]
    assert connected_device.removed == [f"$HOME/.cheese/home/{pid}/{tid}"]
    (archive,) = _stored_archives(pid, tid)
    assert _session_lines(archive) == ['{"type":"user","text":"把它归档"}']
    assert list(archive.parent.glob("*.part")) == []
    async with factory() as session:
        topic = await TopicService(session).get_or_404(tid)
        assert topic.transcripts_archived_at is not None


async def test_a_home_stays_until_its_transcripts_are_stored(
    client, workspace_root, connected_device, monkeypatch
):
    """Only a 2xx from the upload lets the home go. A platform that refuses
    the archive (here: the size cap) and one that cannot be reached both leave
    the home where it is, and the sweep takes it the tick things work again."""
    factory = client.test_factory
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="等着", created_by="u"
        )
        await _seed_device(session, connected_device.device_id)
        await sql_device_service(session).bind_topic_device(
            topic.id, connected_device.device_id, Visibility.host
        )
        await session.commit()
        pid, tid = project.id, topic.id
    home = _home(pid, tid)
    connected_device.homes = [home]
    connected_device.sessions[home] = '{"type":"assistant","text":"..."}'

    monkeypatch.setattr(settings, "transcripts_max_bytes", 16)
    async with factory() as session:
        archived = await TopicService(session).archive(tid, by="u")
        await session.execute(
            update(Topic)
            .where(Topic.id == tid)
            .values(archived_at=datetime.now(UTC) - timedelta(days=10))
        )
        await session.commit()
    # The archive itself is not held up by its disk; the home is.
    assert archived.status == TopicStatus.archived
    assert connected_device.homes == [home] and connected_device.removed == []
    assert _stored_archives(pid, tid) == []
    assert list(Path(settings.transcripts_dir).rglob("*.part")) == []

    monkeypatch.setattr(settings, "transcripts_max_bytes", 512 * 1024 * 1024)
    connected_device.reachable = False
    counts = await sweep_retired_storage(factory, retention_days=7)
    assert counts["homes_left"] == 1 and counts["homes_removed"] == 0
    assert connected_device.homes == [home]
    async with factory() as session:
        topic = await TopicService(session).get_or_404(tid)
        assert topic.transcripts_archived_at is None

    connected_device.reachable = True
    counts = await sweep_retired_storage(factory, retention_days=7)
    assert counts["homes_removed"] == 1 and counts["homes_left"] == 0
    assert connected_device.uploads == [home]
    assert connected_device.homes == []
    assert len(_stored_archives(pid, tid)) == 1
    async with factory() as session:
        topic = await TopicService(session).get_or_404(tid)
        assert topic.transcripts_archived_at is not None


async def test_archive_succeeds_when_the_device_is_offline(client, workspace_root):
    factory = client.test_factory
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="离线", created_by="u"
        )
        await _seed_device(session, "dev-unplugged")
        await sql_device_service(session).bind_topic_device(
            topic.id, "dev-unplugged", Visibility.host
        )
        await session.commit()
        pid, tid = project.id, topic.id
    wt = ws._ensure_worktree(pid, tid)  # noqa: SLF001

    async with factory() as session:
        archived = await TopicService(session).archive(tid, by="u")
        await session.commit()

    # The archive is a fact about the place, not about a machine that is not
    # there: it goes through, the worktree here goes, the home waits for the
    # sweep.
    assert archived.status == TopicStatus.archived
    assert not wt.exists()
    async with factory() as session:
        assert (await TopicService(session).get_or_404(tid)).archived_at is not None


async def test_sweep_removes_leftovers_and_keeps_live_places(
    client, workspace_root, connected_device
):
    factory = client.test_factory
    long_ago = datetime.now(UTC) - timedelta(days=10)
    yesterday = datetime.now(UTC) - timedelta(days=1)

    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        # A machine the project may run on: none of these homes is pinned, so
        # that is what the platform has to go by when it takes their uploads.
        await _seed_device(session, connected_device.device_id, project_id=project.id)
        topics = TopicService(session)
        active = await topics.create(project_id=project.id, title="活", created_by="u")
        old = await topics.create(project_id=project.id, title="旧", created_by="u")
        recent = await topics.create(project_id=project.id, title="近", created_by="u")
        for topic in (old, recent):
            await topics.archive(topic.id, by="u")
        await session.execute(
            update(Topic).where(Topic.id == old.id).values(archived_at=long_ago)
        )
        await session.execute(
            update(Topic).where(Topic.id == recent.id).values(archived_at=yesterday)
        )
        # The active room is on its SECOND tree: the first batch was sealed and
        # the next one has its own id, so its directory is named after a
        # work_trees row and not after any topic.
        trees = WorkTreeService(session)
        first = await trees.ensure_open(project_id=project.id, room_id=active.id)
        await trees.seal(first)
        second = await trees.ensure_open(project_id=project.id, room_id=active.id)
        assert second.id != active.id
        # Two threads in the active room: one closed long ago, one still open.
        tasks = TaskService(session)
        done = await tasks.open_thread(
            project_id=project.id,
            room_id=active.id,
            title="收了",
            owner_handle=None,
            created_by="u",
            agent_instance_id=None,
        )
        going = await tasks.open_thread(
            project_id=project.id,
            room_id=active.id,
            title="还在",
            owner_handle=None,
            created_by="u",
            agent_instance_id=None,
        )
        await session.execute(
            update(Task)
            .where(Task.id == done.id)
            .values(status=TaskStatus.closed, closed_at=long_ago)
        )
        await session.commit()
        pid = project.id
        active_id, old_id, recent_id = active.id, old.id, recent.id
        done_id, going_id, tree_id = done.id, going.id, second.id

    # Leftovers on disk, created AFTER the archives so they stand for what
    # archive could not reach, plus one for a topic that never existed.
    gone_id = uuid.uuid4()
    on_disk = {
        name: ws._ensure_worktree(pid, place)  # noqa: SLF001
        for name, place in {
            "active": active_id,
            "old": old_id,
            "recent": recent_id,
            "gone": gone_id,
        }.items()
    }
    assert on_disk["active"].name == f"topic_{tree_id.hex[:8]}"
    merge = ws._merge_worktree_path(pid) / uuid.uuid4().hex  # noqa: SLF001
    merge.mkdir(parents=True)
    (merge / "MERGE_HEAD").write_text("abc\n")
    connected_device.homes = [
        _home(pid, active_id),
        _home(pid, old_id),
        _home(pid, recent_id),
        _home(pid, gone_id),
        _home(pid, done_id),
        _home(pid, going_id),
        "not-a-project/not-a-place",
    ]
    # Two of the homes to go ran sessions: one for a topic that is still in
    # the database, one for a place that is not. The closed thread's never did.
    connected_device.sessions[_home(pid, old_id)] = '{"topic":"旧"}'
    connected_device.sessions[_home(pid, gone_id)] = '{"topic":"gone"}'

    counts = await sweep_retired_storage(factory, retention_days=7)

    assert counts == {
        "worktrees_removed": 2,
        "worktrees_left": 0,
        "homes_removed": 3,
        "homes_left": 0,
    }
    assert sorted(connected_device.uploads) == sorted(
        [_home(pid, old_id), _home(pid, gone_id)]
    )
    assert _session_lines(_stored_archives(pid, old_id)[0]) == ['{"topic":"旧"}']
    assert _session_lines(_stored_archives(pid, gone_id)[0]) == ['{"topic":"gone"}']
    assert _stored_archives(pid, done_id) == []
    async with factory() as session:
        assert (
            await TopicService(session).get_or_404(old_id)
        ).transcripts_archived_at is not None
    assert not on_disk["old"].exists() and not on_disk["gone"].exists()
    assert on_disk["active"].is_dir() and on_disk["recent"].is_dir()
    assert (merge / "MERGE_HEAD").read_text() == "abc\n"
    registered = _registered_worktrees(pid)
    assert str(on_disk["active"]) in registered
    assert str(on_disk["old"]) not in registered
    assert sorted(connected_device.removed) == sorted(
        f"$HOME/.cheese/home/{pid}/{place}" for place in (old_id, gone_id, done_id)
    )
    assert sorted(connected_device.homes) == sorted(
        [
            _home(pid, active_id),
            _home(pid, recent_id),
            _home(pid, going_id),
            "not-a-project/not-a-place",
        ]
    )


async def test_sweep_leaves_a_worktree_whose_prefix_names_two_places(
    client, workspace_root
):
    """Eight hex digits are all the directory name carries. Two places sharing
    them is unlikely, and deleting either one's tree on a guess is not an
    option — so the directory stays, and the log says why."""
    factory = client.test_factory
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        prefix = "0badcafe"
        twins = [
            Topic(
                id=uuid.UUID(prefix + uuid.uuid4().hex[8:]),
                project_id=project.id,
                parent_id=project.root_topic_id,
                title=f"孪生{i}",
                kind=TopicKind.topic,
                status=TopicStatus.archived,
                archived_at=datetime.now(UTC) - timedelta(days=30),
            )
            for i in range(2)
        ]
        session.add_all(twins)
        await session.commit()
        pid, first = project.id, twins[0].id

    wt = ws._ensure_worktree(pid, first)  # noqa: SLF001
    assert wt.name == f"topic_{prefix}"

    counts = await sweep_retired_storage(factory, retention_days=7)

    assert counts["worktrees_removed"] == 0
    assert wt.is_dir()


async def test_the_upload_route_believes_the_pin_first_and_the_project_after(
    client, workspace_root
):
    """The machine a place is pinned to may store its transcripts and no other
    may, whatever else it serves; with no pin to go by, a machine the project
    may run on is believed. Nothing but a device token opens the route."""
    factory = client.test_factory
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="钉住", created_by="u"
        )
        await _seed_device(session, "dev-ran-it")
        await _seed_device(session, "dev-other", project_id=project.id)
        await sql_device_service(session).bind_topic_device(
            topic.id, "dev-ran-it", Visibility.host
        )
        await session.commit()
        pid, tid = project.id, topic.id
    url = f"/connector/transcripts/{pid}/{tid}"
    body = _home_archive("{}")

    assert (await _put(url, body, token=None)).status_code == 401
    assert (await _put(url, body, token="tok-dev-other")).status_code == 403
    assert _stored_archives(pid, tid) == []

    stored = await _put(url, body, token="tok-dev-ran-it")
    assert stored.status_code == 200, stored.text
    assert stored.json()["size"] == len(body)
    assert stored.json()["sha256"] == hashlib.sha256(body).hexdigest()
    assert len(_stored_archives(pid, tid)) == 1
    # A second upload for the same place is a second file, never an overwrite.
    assert (await _put(url, body, token="tok-dev-ran-it")).status_code == 200
    assert len(_stored_archives(pid, tid)) == 2

    orphan = f"/connector/transcripts/{pid}/{uuid.uuid4()}"
    assert (await _put(orphan, body, token="tok-dev-other")).status_code == 200
    assert (await _put(orphan, body, token="tok-dev-ran-it")).status_code == 403
    nowhere = f"/connector/transcripts/{uuid.uuid4()}/{tid}"
    assert (await _put(nowhere, body, token="tok-dev-ran-it")).status_code == 404


async def test_the_upload_route_keeps_nothing_it_refuses(
    client, workspace_root, monkeypatch
):
    factory = client.test_factory
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="太大", created_by="u"
        )
        await _seed_device(session, "dev-big", project_id=project.id)
        await session.commit()
        pid, tid = project.id, topic.id
    url = f"/connector/transcripts/{pid}/{tid}"
    place = Path(settings.transcripts_dir) / str(pid) / str(tid)

    monkeypatch.setattr(settings, "transcripts_max_bytes", 1024)
    # Random bytes, so gzip cannot shrink the body back under the cap.
    too_big = _home_archive(os.urandom(4096).hex())
    assert len(too_big) > 1024
    assert (await _put(url, too_big, token="tok-dev-big")).status_code == 413
    assert list(place.rglob("*")) == []

    monkeypatch.setattr(settings, "transcripts_max_bytes", 512 * 1024 * 1024)
    assert (await _put(url, b"not a tar.gz", token="tok-dev-big")).status_code == 400
    truncated = _home_archive("{}")[:-8]
    assert (await _put(url, truncated, token="tok-dev-big")).status_code == 400
    assert list(place.rglob("*")) == []
