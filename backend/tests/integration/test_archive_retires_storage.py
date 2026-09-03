"""Archive takes a place's disk with it, and the sweep takes what archive missed.

A room's git worktree on this box and its isolated home on the device that ran
it used to outlive the room forever (dev box, 2026-09-03: 141 GB of worktrees
and 162 GB of homes, most of them for places long gone). These tests drive the
real `git worktree` layout under a temporary workspace root and a fake connector
attached to the real ``DeviceHub`` — the device end of the link, answering
``exec`` frames the way the Go connector does — so what is asserted is what a
box and a device actually end up with, not which helper was called.
"""

import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.domain.agent.device_hub import device_hub
from app.domain.device.models import DeviceRow
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

pytestmark = pytest.mark.anyio


class FakeConnector:
    """A device's end of the link: records every command the platform runs on
    it and answers as a shell would — a listing of the homes it holds, and
    ``rm -rf`` removing one of them."""

    def __init__(self, device_id: str, homes: list[str] | None = None) -> None:
        self.device_id = device_id
        self.homes = list(homes or [])  # "<project>/<place>" pairs on disk
        self.removed: list[str] = []  # home paths an rm reached

    async def send_json(self, msg: dict) -> None:
        if msg.get("t") != "exec":
            return
        script = msg["command"][-1]
        stdout = ""
        if "for p in */*" in script:
            stdout = "".join(f"{h}\n" for h in self.homes)
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
                "stderr": "",
                "exit": 0,
                "truncated": False,
            },
        )


@pytest.fixture
def workspace_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "ws"
    monkeypatch.setattr(settings, "workspace_root", str(root))
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


async def _seed_device(session, device_id: str) -> None:
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
    await session.flush()


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

    counts = await sweep_retired_storage(factory, retention_days=7)

    assert counts == {
        "worktrees_removed": 2,
        "worktrees_left": 0,
        "homes_removed": 3,
        "homes_left": 0,
    }
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
