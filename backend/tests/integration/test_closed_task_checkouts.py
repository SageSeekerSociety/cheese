"""A closed task's checkout leaves the machines its room worked on.

Only once its work is on the forge: a checkout holding uncommitted files,
unpushed commits or a running process stays. A machine that was offline when
the task closed — the one a room has left included — is swept when it
connects again.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.agent import device_hub as hub_module
from app.domain.agent_session.services import AgentSessionService
from app.domain.identity.services import IdentityService
from app.domain.room_task.checkouts import remove_closed_checkouts
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.services import TaskService
from tests.integration.conftest import post_project, session_auth_headers

pytestmark = pytest.mark.anyio


def git(cwd, *args):
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


class Machine:
    """A machine's disk as a room leaves it: a room home with the project's
    repository cache and one checkout per task, next to a forge."""

    def __init__(self, root: Path, project: uuid.UUID, resource: str):
        self.root = root
        self.home = root / ".cheese/home" / str(project) / resource
        forge = root / "forge.git"
        if not forge.exists():
            seed = root / "seed"
            seed.mkdir(parents=True)
            git(seed, "init", "-b", "main")
            (seed / "README").write_text("base\n")
            git(seed, "add", ".")
            git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "b")
            git(root, "clone", "--bare", str(seed), str(forge))
        self.cache = self.home / ".cheese/repositories" / f"{project}.git"
        self.cache.parent.mkdir(parents=True)
        git(self.cache.parent, "init", "--bare", str(self.cache))
        git(self.cache, "remote", "add", "origin", str(forge))
        git(self.cache, "fetch", "origin", "main")

    def checkout(self, task: uuid.UUID, *, push: bool) -> Path:
        path = self.home / ".cheese/tasks" / str(task)
        branch = f"task/{task.hex[:8]}"
        git(self.cache, "worktree", "add", "-b", branch, str(path), "origin/main")
        (path / "work.txt").write_text(f"{task}\n")
        git(path, "add", ".")
        git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "w")
        if push:
            git(path, "push", "origin", branch)
        return path


def _hub(monkeypatch, machines: dict[str, Machine], online: set[str]):
    """The device hub, with each machine's exec run as that machine would."""
    asked = asyncio.Event()
    ran: list[str] = []

    def is_online(device):
        asked.set()
        return device in online

    async def exec_(device, argv, *, stdin, timeout):
        machine = machines[device]
        result = subprocess.run(
            [sys.executable, *argv[1:]],
            input=stdin,
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(machine.root)},
            timeout=timeout,
        )
        ran.append(device)
        return {
            "exit": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    fake = SimpleNamespace(
        target=lambda _device: "linux-amd64", is_online=is_online, exec=exec_
    )
    monkeypatch.setattr(hub_module, "device_hub", fake)
    return SimpleNamespace(asked=asked, ran=ran)


async def _room(client, tmp_path, *, retained: bool = False):
    """A room whose session works on machine "a" — and, with ``retained``,
    has earlier worked on machine "b" and left it."""
    project = post_project(client, json={"name": "Checkouts"}, owner="alice").json()[
        "data"
    ]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(room["id"])
    machines, leases = {}, {}
    for device in ("a", "b") if retained else ("a",):
        resource = str(uuid.uuid4())
        machines[device] = Machine(tmp_path / device, project_id, resource)
        leases[device] = {
            "kind": "device",
            "device_id": device,
            "resource_id": resource,
        }
    async with client.test_factory() as db:
        agent = await IdentityService(db).ensure_room_agent_user(topic_id)
        session = await AgentSessionService(db).ensure(
            topic_id, agent.username, harness="claude-code"
        )
        session.work_lease = {**leases["a"], "status": "ready"}
        session.execution_request = {
            "generation": str(uuid.uuid4()),
            "retained_leases": [leases["b"]] if retained else [],
        }
        await db.commit()
    return SimpleNamespace(project_id=project_id, topic_id=topic_id, machines=machines)


async def _tasks(client, room, count: int) -> list[uuid.UUID]:
    async with client.test_factory() as db:
        tasks = [
            Task(project_id=room.project_id, room_id=room.topic_id, title=f"t{n}")
            for n in range(count)
        ]
        db.add_all(tasks)
        await db.commit()
        return [task.id for task in tasks]


async def _close(client, room, tasks: list[uuid.UUID]) -> None:
    """Close them the way the room does, and commit."""
    async with client.test_factory() as db:
        service = TaskService(db)
        for task in tasks:
            row = await db.get(Task, task)
            assert row is not None
            await service.close_thread(row)
        await db.commit()


async def _until(condition, timeout: float = 30.0) -> None:
    async def poll():
        while not condition():
            await asyncio.sleep(0.05)

    await asyncio.wait_for(poll(), timeout)


async def test_closing_removes_published_checkouts_and_keeps_unpublished_ones(
    client, tmp_path, monkeypatch
):
    room = await _room(client, tmp_path)
    machine = room.machines["a"]
    published, unpushed, edited, busy, still_open = await _tasks(client, room, 5)
    paths = {
        published: machine.checkout(published, push=True),
        unpushed: machine.checkout(unpushed, push=False),
        edited: machine.checkout(edited, push=True),
        busy: machine.checkout(busy, push=True),
        still_open: machine.checkout(still_open, push=True),
    }
    (paths[edited] / "draft.txt").write_text("not committed\n")
    hub = _hub(monkeypatch, room.machines, online={"a"})
    inside = subprocess.Popen(["sleep", "60"], cwd=paths[busy])
    try:
        await _close(client, room, [published, unpushed, edited, busy])
        await _until(lambda: hub.ran == ["a"])
    finally:
        inside.kill()
        inside.wait()

    assert not paths[published].exists()
    assert str(paths[published]) not in git(machine.cache, "worktree", "list")
    for kept in (unpushed, edited, busy, still_open):
        assert paths[kept].exists()
    assert (paths[edited] / "draft.txt").read_text() == "not committed\n"


async def test_a_machine_offline_at_close_is_swept_when_it_connects(
    client, tmp_path, monkeypatch
):
    room = await _room(client, tmp_path, retained=True)
    (task,) = await _tasks(client, room, 1)
    current = room.machines["a"].checkout(task, push=True)
    left = room.machines["b"].checkout(task, push=True)
    hub = _hub(monkeypatch, room.machines, online={"a"})

    await _close(client, room, [task])
    await _until(lambda: hub.ran == ["a"])
    assert not current.exists()
    # The machine the room left was offline: its checkout is still there.
    assert left.exists()

    hub = _hub(monkeypatch, room.machines, online={"a", "b"})
    counts = await remove_closed_checkouts(client.test_factory, device_id="b")

    assert hub.ran == ["b"]
    assert counts == {"removed": 1, "kept": 0}
    assert not left.exists()


async def test_open_tasks_are_never_named(client, tmp_path, monkeypatch):
    room = await _room(client, tmp_path)
    (task,) = await _tasks(client, room, 1)
    path = room.machines["a"].checkout(task, push=True)
    hub = _hub(monkeypatch, room.machines, online={"a"})

    counts = await remove_closed_checkouts(client.test_factory, device_id="a")

    assert counts == {"removed": 0, "kept": 0}
    assert hub.ran == []
    assert path.exists()
    async with client.test_factory() as db:
        row = await db.get(Task, task)
        assert row is not None and row.status is TaskStatus.open


async def test_a_room_with_a_thousand_closed_tasks_is_swept_on_windows(
    client, tmp_path, monkeypatch
):
    """Windows refuses a command line past 32,767 characters; naming a
    thousand closed tasks at once would pass it, and nothing would ever be
    removed from that machine."""
    room = await _room(client, tmp_path)
    machine = room.machines["a"]
    tasks = await _tasks(client, room, 1000)
    published, unpushed = tasks[-1], tasks[0]
    gone = machine.checkout(published, push=True)
    stays = machine.checkout(unpushed, push=False)
    async with client.test_factory() as db:
        for task in tasks:
            row = await db.get(Task, task)
            assert row is not None
            row.status = TaskStatus.closed
        await db.commit()
    _hub(monkeypatch, room.machines, online={"a"})
    run_on_machine = hub_module.device_hub.exec

    async def windows_exec(device, argv, *, stdin, timeout):
        # CreateProcess counts the whole command line, separators included.
        if len(" ".join(argv)) > 32767:
            return {
                "exit": 1,
                "stdout": "",
                "stderr": "fork/exec python3.exe: "
                "The filename or extension is too long.",
            }
        return await run_on_machine(device, argv, stdin=stdin, timeout=timeout)

    monkeypatch.setattr(hub_module.device_hub, "exec", windows_exec)

    counts = await remove_closed_checkouts(client.test_factory, device_id="a")

    assert counts == {"removed": 1, "kept": 1}
    assert not gone.exists()
    assert stays.exists()
