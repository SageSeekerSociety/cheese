"""A task's live files are read from the machine its own session took.

Every session in a channel works in a home of its own, so one task's working
copy is never in another task's sandbox. These tests seat other tasks of the
same channel on machines that are gone, as a channel's older tasks are once
their sandboxes were released, and check that what the 改动 tab is told
depends on this task's own machine only.
"""

import asyncio
import re
import subprocess
import uuid

import pytest

from app.domain.agent import execution
from app.domain.agent.device_contract import DeviceCallError
from app.domain.agent.harness import harness_for
from app.domain.agent.harness.claude_code.remote_execution.runtime import Executor
from app.domain.agent_session import attachment as attachments
from app.domain.agent_session.repositories import AgentSessionRepository
from app.domain.environment import services as environment_rows
from app.domain.room_task.models import Task
from app.domain.topic.models import Topic
from tests.delivery import delivery_task
from tests.integration.conftest import _register, session_auth_headers
from tests.integration.test_file_panel_safety import _mkproject, _owner
from tests.machine_work import machine_commits, machine_keeps
from tests.support.environments import remember_place

GONE = "dial unix /tmp/cheese-execution-3f2a.sock: connect: no such file or directory"


@pytest.fixture
def machines(client, monkeypatch, tmp_path):
    """Machines by device id: `working` holds task homes, `gone` was destroyed,
    `failing` answers with an error of its own. Records which were asked."""
    executor = object.__new__(Executor)
    executor.env = {"HOME": str(tmp_path / "working")}
    asked = []

    async def call(target, method, params):
        asked.append(target["device_id"])
        if target["device_id"] == "gone":
            raise DeviceCallError(GONE)
        if target["device_id"] == "failing":
            raise DeviceCallError("git: index.lock exists")
        answer = executor.task_fs(params)
        if target["device_id"] == "old":
            # An executor installed before `not_found` said what is missing.
            answer.pop("missing", None)
        return answer

    monkeypatch.setattr(execution, "call", call)
    return tmp_path / "working", asked


def _room(client, pid) -> uuid.UUID:
    response = client.post("/topics", json={"project_id": str(pid), "title": "Files"})
    assert response.status_code == 200
    return uuid.UUID(response.json()["data"]["id"])


def _seat(
    client,
    task,
    *,
    device: str | None,
    lost=False,
    parked=False,
    generation=None,
    lease=None,
):
    """The task's own session takes a machine (`device`), or is placed with
    none yet (None), as a cloud session is before its first tool call.
    ``lease`` records that lease as it is instead. ``lost``: that sandbox was
    destroyed since; ``parked``: its processes were stopped since, its home
    left where it was."""

    async def place():
        async with client.test_factory() as session:
            room = await session.get(Topic, task.room_id)
            current = str(room.resource_id or room.id)
            await remember_place(
                session,
                conversation_id=task.id,
                agent_handle="cheese",
                environment=lease
                or (
                    {
                        "kind": "device",
                        "device_id": device,
                        # The room generation its environment was made for.
                        **({"room_resource_id": generation} if generation else {}),
                    }
                    if device is not None
                    else None
                ),
                runtime_location={
                    "device_id": "session-host",
                    "channel": "central",
                    "resource_id": generation or current,
                },
                harness=harness_for(None),
            )
            if lost or parked:
                row = await AgentSessionRepository(session).get(
                    task.id, "cheese", harness_for(None)
                )
                # What the sweep leaves: the lease says nothing answers on it,
                # and the agent is owed a notice.
                if lost:
                    await attachments.lose(session, row, device)
                else:
                    await attachments.park(session, row, device, ["npm run dev"])
            await session.commit()

    asyncio.run(place())


def _channel_with_released_older_task(client, *, commit=True):
    """A channel whose earlier task's sandbox was released, and a new task."""
    pid = _mkproject(client)
    room = _room(client, pid)
    older = delivery_task(client, room)
    _seat(client, older, device="gone", lost=True)
    task = delivery_task(client, room, new=True, commit=commit)
    return pid, room, task


def _published(client, task):
    """The task's pushed branch has its pull request, as every pushed task gets."""

    async def publish():
        async with client.test_factory() as session:
            row = await session.get(Task, task.id)
            row.pr_number = int(task.id.hex[:6], 16)
            await session.commit()

    asyncio.run(publish())


def _badge(client, pid, task) -> list[str]:
    return client.get(
        f"/projects/{pid}/topics/{task.id}/work-summary", headers=_owner(client)
    ).json()["data"]["changed_files"]


def _diffed(text: str) -> list[str]:
    return sorted(re.findall(r"^diff --git a/(\S+) b/", text, re.M))


def _live(client, pid, room, task, what):
    path = {"files": "files", "diff": "git/diff"}[what]
    return client.get(
        f"/projects/{pid}/{path}",
        params={"source": "live", "topic": str(room), "task": str(task.id)},
        headers=_owner(client),
    )


@pytest.mark.parametrize("seated", [False, True])
def test_a_task_with_no_machine_of_its_own_yet_has_no_changes(client, machines, seated):
    # Its session may already be placed, before its first tool call has taken
    # a sandbox; the older task's released sandbox is not its.
    _, asked = machines
    pid, room, task = _channel_with_released_older_task(client, commit=False)
    if seated:
        _seat(client, task, device=None)

    listing = _live(client, pid, room, task, "files")
    diff = _live(client, pid, room, task, "diff")

    assert listing.status_code == 200, listing.text
    assert listing.json()["data"]["data"] == []
    assert diff.status_code == 200, diff.text
    assert diff.json()["data"]["diff"] == ""
    assert asked == []


@pytest.mark.parametrize("seated", [False, True])
def test_pushed_work_shows_while_no_machine_holds_the_task(client, machines, seated):
    # The 改动 badge counts the branch's changes against its base. With no
    # working copy on any machine, the tab's list, its diff and a file a chip
    # opens are those same changes, not an empty working copy.
    _, asked = machines
    pid, room, task = _channel_with_released_older_task(client)
    machine_commits(pid, task.id, {"backend/usage/models.py": "POOL = 1\n"})
    _published(client, task)
    if seated:
        _seat(client, task, device=None)

    badge = _badge(client, pid, task)
    diff = _live(client, pid, room, task, "diff")
    listing = _live(client, pid, room, task, "files")
    read = client.get(
        f"/projects/{pid}/file",
        params={
            "source": "live",
            "path": "backend/usage/models.py",
            "topic": str(room),
            "task": str(task.id),
        },
        headers=_owner(client),
    )

    assert badge == ["backend/usage/models.py", f"deliveries/{task.id}.txt"]
    assert diff.status_code == 200, diff.text
    assert _diffed(diff.json()["data"]["diff"]) == badge
    # Asked for live, answered off the forge: each answer says so, and the
    # page marks what it shows as the committed version.
    assert diff.json()["data"]["source"] == "committed"
    assert listing.status_code == 200, listing.text
    assert set(badge) <= {f["path"] for f in listing.json()["data"]["data"]}
    assert listing.json()["data"]["source"] == "committed"
    assert read.status_code == 200, read.text
    assert read.json()["data"]["content"] == "POOL = 1\n"
    assert read.json()["data"]["source"] == "committed"
    assert read.json()["data"]["editable"] is False
    assert asked == []


def test_pushed_work_shows_while_its_machine_has_no_copy_of_it(client, machines):
    # Its session holds a machine whose home has no directory for the task,
    # as a fresh sandbox does before `cheese worktree` runs in it.
    _, asked = machines
    pid, room, task = _channel_with_released_older_task(client)
    _published(client, task)
    _seat(client, task, device="working")

    badge = _badge(client, pid, task)
    diff = _live(client, pid, room, task, "diff")
    listing = _live(client, pid, room, task, "files")

    assert badge == [f"deliveries/{task.id}.txt"]
    assert _diffed(diff.json()["data"]["diff"]) == badge
    assert set(badge) <= {f["path"] for f in listing.json()["data"]["data"]}
    assert asked == ["working", "working"]


def test_pushed_work_shows_while_its_machine_is_still_being_set_up(client):
    # The session reserved a machine, but its executor never came up there
    # (setup is still running, or it failed and the next tool call retries).
    # Nothing on that machine holds the task yet, so the panel shows the
    # branch, as it does before any machine is taken.
    pid, room, task = _channel_with_released_older_task(client)
    machine_commits(pid, task.id, {"backend/usage/models.py": "POOL = 1\n"})
    _published(client, task)
    _seat(
        client,
        task,
        device="working",
        lease={
            "kind": "device",
            "device_id": "working",
            "resource_id": str(uuid.uuid4()),
            "status": "preparing",
        },
    )

    listing = _live(client, pid, room, task, "files")
    diff = _live(client, pid, room, task, "diff")
    read = _read(client, pid, room, task, "backend/usage/models.py")

    assert listing.status_code == 200, listing.text
    assert listing.json()["data"]["source"] == "committed"
    assert "backend/usage/models.py" in {
        f["path"] for f in listing.json()["data"]["data"]
    }
    assert diff.status_code == 200, diff.text
    assert "backend/usage/models.py" in _diffed(diff.json()["data"]["diff"])
    assert read.status_code == 200, read.text
    assert read.json()["data"]["content"] == "POOL = 1\n"


def test_a_machine_still_finishing_setup_with_a_copy_is_read(client, machines):
    # Setup got far enough to record where the executor is, and the task's
    # copy on that machine has work nobody pushed. That work is what the
    # panel shows, not the branch.
    home, asked = machines
    pid, room, task = _channel_with_released_older_task(client)
    _seat(
        client,
        task,
        device="working",
        lease={
            "kind": "device",
            "device_id": "working",
            "resource_id": str(uuid.uuid4()),
            "status": "preparing",
            "home": str(home),
            "state": str(home / ".cheese/executor"),
        },
    )
    worktree = home / ".cheese/tasks" / str(task.id)
    worktree.mkdir(parents=True)
    subprocess.run(["git", "init", str(worktree)], check=True, capture_output=True)
    (worktree / "draft.md").write_text("draft\n")

    listing = _live(client, pid, room, task, "files")

    assert listing.status_code == 200, listing.text
    assert [f["path"] for f in listing.json()["data"]["data"]] == ["draft.md"]


def _read(client, pid, room, task, path):
    return client.get(
        f"/projects/{pid}/file",
        params={
            "source": "live",
            "path": path,
            "topic": str(room),
            "task": str(task.id),
        },
        headers=_owner(client),
    )


def test_a_file_reads_from_the_branch_while_its_machine_has_no_copy_of_it(
    client, machines
):
    # The machine answers, but its home has no directory for the task yet: one
    # file of the pushed branch reads like the listing and the diff do.
    _, asked = machines
    pid, room, task = _channel_with_released_older_task(client)
    machine_commits(pid, task.id, {"backend/usage/models.py": "POOL = 1\n"})
    _published(client, task)
    _seat(client, task, device="working")

    read = _read(client, pid, room, task, "backend/usage/models.py")
    raw = client.get(
        f"/projects/{pid}/file/raw",
        params={
            "source": "live",
            "path": "backend/usage/models.py",
            "topic": str(room),
            "task": str(task.id),
        },
        headers=_owner(client),
    )
    absent = _read(client, pid, room, task, "backend/usage/missing.py")

    assert read.status_code == 200, read.text
    assert read.json()["data"]["content"] == "POOL = 1\n"
    assert read.json()["data"]["source"] == "committed"
    assert read.json()["data"]["editable"] is False
    assert raw.status_code == 200, raw.text
    assert raw.content == b"POOL = 1\n"
    assert raw.headers["X-Cheese-File-Source"] == "committed"
    assert absent.status_code == 404, absent.text
    assert "working" in asked


def test_a_file_missing_from_a_present_copy_is_not_read_from_the_branch(
    client, machines
):
    # The copy is on the machine and the file is not in it (deleted there, say):
    # the branch's version of it is not the task's file.
    home, _ = machines
    pid, room, task = _channel_with_released_older_task(client)
    machine_commits(pid, task.id, {"backend/usage/models.py": "POOL = 1\n"})
    _published(client, task)
    _seat(client, task, device="working")
    worktree = home / ".cheese/tasks" / str(task.id)
    worktree.mkdir(parents=True)
    subprocess.run(["git", "init", str(worktree)], check=True, capture_output=True)

    read = _read(client, pid, room, task, "backend/usage/models.py")

    assert read.status_code == 404, read.text
    assert read.json()["error"]["message"] == "环境里还没有这个任务文件"


def test_an_older_executor_without_a_copy_still_says_the_file_is_not_there(
    client, machines
):
    # Its `not_found` cannot tell a missing directory from a missing file, so a
    # file stays "not on the machine"; the listing still comes from the branch.
    _, asked = machines
    pid, room, task = _channel_with_released_older_task(client)
    machine_commits(pid, task.id, {"backend/usage/models.py": "POOL = 1\n"})
    _published(client, task)
    _seat(client, task, device="old")

    read = _read(client, pid, room, task, "backend/usage/models.py")
    listing = _live(client, pid, room, task, "files")

    assert read.status_code == 404, read.text
    assert read.json()["error"]["message"] == "环境里还没有这个任务文件"
    assert listing.status_code == 200, listing.text
    assert "backend/usage/models.py" in {
        f["path"] for f in listing.json()["data"]["data"]
    }
    assert asked == ["old", "old"]


def test_a_task_reads_its_own_machine_not_the_newest_in_the_channel(client, machines):
    home, asked = machines
    pid, room, task = _channel_with_released_older_task(client)
    _seat(client, task, device="working")
    later = delivery_task(client, room, new=True)
    _seat(client, later, device="gone", lost=True)
    worktree = home / ".cheese/tasks" / str(task.id)
    worktree.mkdir(parents=True)
    subprocess.run(["git", "init", str(worktree)], check=True, capture_output=True)
    (worktree / "draft.md").write_text("draft\n")

    listing = _live(client, pid, room, task, "files")

    assert listing.status_code == 200, listing.text
    assert [f["path"] for f in listing.json()["data"]["data"]] == ["draft.md"]
    assert asked == ["working"]


def test_changes_its_machine_answers_are_said_to_be_live(client, machines):
    home, _ = machines
    pid, room, task = _channel_with_released_older_task(client)
    _seat(client, task, device="working")
    worktree = home / ".cheese/tasks" / str(task.id)
    worktree.mkdir(parents=True)
    git = ["git", "-C", str(worktree), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", str(worktree)], check=True, capture_output=True)
    (worktree / "README.md").write_text("start\n")
    for step in (
        ["add", "."],
        ["-c", "commit.gpgsign=false", "commit", "-m", "start"],
        ["update-ref", "refs/remotes/origin/main", "HEAD"],
    ):
        subprocess.run([*git, *step], check=True, capture_output=True)
    (worktree / "draft.md").write_text("draft\n")

    diff = _live(client, pid, room, task, "diff")

    assert diff.status_code == 200, diff.text
    assert diff.json()["data"]["source"] == "live"


@pytest.mark.parametrize("device", ["failing", "gone"])
def test_a_failing_machine_is_not_reported_as_released(client, machines, device):
    # The machine's own words name its sockets and paths; the person is told
    # it did not answer, and where they can still look.
    pid, room, task = _channel_with_released_older_task(client)
    _seat(client, task, device=device)

    response = _live(client, pid, room, task, "diff")

    assert response.status_code == 503, response.text
    # Said as an environment that did not answer, which the page offers to
    # ask for again; not as one that is gone.
    assert response.json()["error"]["name"] == "EnvironmentNotResponding"
    message = response.json()["error"]["message"]
    assert "已释放" not in message
    assert "无法响应" in message
    assert "index.lock" not in message and "sock" not in message


@pytest.mark.parametrize("gone", ["lost", "parked"])
def test_a_task_whose_machine_is_gone_reads_its_kept_work_from_the_forge(
    client, machines, gone
):
    # Its sandbox was destroyed, or parked with its processes stopped, after a
    # person's edit was kept off it. Opening the task's files answers what
    # that edit left — the file it changed and the one it added — without
    # asking any machine, and not the socket the machine no longer listens on.
    _, asked = machines
    pid, room, task = _channel_with_released_older_task(client)
    machine_commits(pid, task.id, {"backend/usage/models.py": "POOL = 1\n"})
    _published(client, task)
    machine_keeps(
        pid,
        task.id,
        {"backend/usage/models.py": "POOL = 2\n", "notes/draft.md": "draft\n"},
    )
    _seat(client, task, device="gone", **{gone: True})

    read = _read(client, pid, room, task, "backend/usage/models.py")
    added = _read(client, pid, room, task, "notes/draft.md")
    raw = client.get(
        f"/projects/{pid}/file/raw",
        params={
            "source": "live",
            "path": "notes/draft.md",
            "topic": str(room),
            "task": str(task.id),
        },
        headers=_owner(client),
    )
    listing = _live(client, pid, room, task, "files")
    diff = _live(client, pid, room, task, "diff")

    assert read.status_code == 200, read.text
    assert read.json()["data"]["content"] == "POOL = 2\n"
    assert read.json()["data"]["editable"] is False
    assert added.status_code == 200, added.text
    assert added.json()["data"]["content"] == "draft\n"
    assert raw.status_code == 200, raw.text
    assert raw.content == b"draft\n"
    assert listing.status_code == 200, listing.text
    assert {"backend/usage/models.py", "notes/draft.md"} <= {
        f["path"] for f in listing.json()["data"]["data"]
    }
    assert diff.status_code == 200, diff.text
    assert "backend/usage/models.py" in _diffed(diff.json()["data"]["diff"])
    assert asked == []


def test_kept_work_the_branch_moved_past_is_not_shown_as_the_tasks_files(
    client, machines
):
    # A new checkout lays kept work back only over the head it was cut from;
    # once the branch moved on, the task's files are the branch's.
    _, asked = machines
    pid, room, task = _channel_with_released_older_task(client)
    machine_commits(pid, task.id, {"backend/usage/models.py": "POOL = 1\n"})
    _published(client, task)
    machine_keeps(pid, task.id, {"backend/usage/models.py": "POOL = 2\n"})
    machine_commits(pid, task.id, {"backend/usage/models.py": "POOL = 3\n"})
    _seat(client, task, device="gone", lost=True)

    read = _read(client, pid, room, task, "backend/usage/models.py")

    assert read.status_code == 200, read.text
    assert read.json()["data"]["content"] == "POOL = 3\n"
    assert read.json()["data"]["source"] == "committed"
    assert asked == []


def test_the_hand_in_panel_opens_while_the_tasks_machine_is_parked(client, machines):
    # Parked with its processes stopped, the sandbox's executor socket is gone;
    # the panel lists what the branch would hand in rather than failing on it.
    _, asked = machines
    pid, room, task = _channel_with_released_older_task(client)
    machine_commits(pid, task.id, {"backend/usage/models.py": "POOL = 1\n"})
    _published(client, task)
    _seat(client, task, device="gone", parked=True)

    panel = client.get(f"/topics/{task.id}/hand-in/panel", headers=_owner(client))

    assert panel.status_code == 200, panel.text
    assert "backend/usage/models.py" in {
        f["path"] for f in panel.json()["data"]["files"]
    }
    assert asked == []


def test_a_machine_of_an_earlier_room_generation_is_not_read(client, machines):
    _, asked = machines
    pid, room, task = _channel_with_released_older_task(client)
    _seat(client, task, device="working", generation=str(uuid.uuid4()))

    listing = _live(client, pid, room, task, "files")

    assert listing.status_code == 200, listing.text
    assert listing.json()["data"]["source"] == "committed"
    assert asked == []


def _mentor(client, pid, handle: str) -> dict[str, str]:
    """``handle`` joins the project as a mentor: reads it, edits nothing."""
    _register(client, handle)
    invited = client.post(
        f"/projects/{pid}/invitations",
        json={"user_handle": handle, "role": "mentor"},
        headers=session_auth_headers("alice"),
    )
    assert invited.status_code == 200, invited.text
    accepted = client.post(
        f"/invitations/{invited.json()['data']['id']}/respond",
        json={"accept": True},
        headers=session_auth_headers(handle),
    )
    assert accepted.status_code == 200, accepted.text
    return session_auth_headers(handle)


def test_a_reader_who_may_not_edit_is_told_a_search_needs_the_environment(
    client, machines
):
    # A search brings the task's environment up only for someone who may edit
    # its files. A reader who may only look is told plainly that the search
    # needs it running, and nothing is started for them.
    _, asked = machines
    pid, room, task = _channel_with_released_older_task(client)
    machine_commits(pid, task.id, {"notes.md": "a needle here\n"})
    _published(client, task)
    _seat(client, task, device="gone", lost=True)
    reader = _mentor(client, pid, "teacher")

    response = client.get(
        f"/projects/{pid}/files/search",
        params={"q": "needle", "topic": str(room), "task": str(task.id)},
        headers=reader,
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["name"] == "SearchNeedsEnvironment"
    assert response.json()["error"]["retryable"] is False

    async def preparing():
        async with client.test_factory() as session:
            environment = await environment_rows.environment_of_conversation(
                session, task.id
            )
            return environment is not None and environment.preparing_until is not None

    assert asyncio.run(preparing()) is False
    assert asked == []
