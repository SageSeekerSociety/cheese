"""A task's live files are read from the machine its own session took.

Every session in a channel works in a home of its own, so one task's working
copy is never in another task's sandbox. These tests seat other tasks of the
same channel on machines that are gone, as a channel's older tasks are once
their sandboxes were released, and check that what the 改动 tab is told
depends on this task's own machine only.
"""

import asyncio
import subprocess
import uuid

import pytest

from app.domain.agent import execution
from app.domain.agent.device_contract import DeviceCallError
from app.domain.agent.harness import harness_for
from app.domain.agent.harness.claude_code.remote_execution.runtime import Executor
from app.domain.agent_session.models import LOST_KEY
from app.domain.agent_session.repositories import AgentSessionRepository
from app.domain.agent_session.services import AgentSessionService
from app.domain.topic.models import Topic
from tests.delivery import delivery_task
from tests.integration.test_file_panel_safety import _mkproject, _owner

GONE = "lstat /home/cheese/.cheese/home/p/r/.cheese/executor: no such file"


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
        return executor.task_fs(params)

    monkeypatch.setattr(execution, "call", call)
    return tmp_path / "working", asked


def _room(client, pid) -> uuid.UUID:
    response = client.post("/topics", json={"project_id": str(pid), "title": "Files"})
    assert response.status_code == 200
    return uuid.UUID(response.json()["data"]["id"])


def _seat(client, task, *, device: str | None, lost=False, generation=None):
    """The task's own session takes a machine (`device`), or is placed with
    none yet (None), as a cloud session is before its first tool call."""

    async def place():
        async with client.test_factory() as session:
            room = await session.get(Topic, task.room_id)
            current = str(room.resource_id or room.id)
            await AgentSessionService(session).remember_place(
                conversation_id=task.id,
                agent_handle="cheese",
                work_lease=(
                    {"kind": "device", "device_id": device}
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
            if lost:
                row = await AgentSessionRepository(session).get(
                    task.id, "cheese", harness_for(None)
                )
                row.execution_request = {LOST_KEY: True}
            await session.commit()

    asyncio.run(place())


def _channel_with_released_older_task(client):
    """A channel whose earlier task's sandbox was released, and a new task."""
    pid = _mkproject(client)
    room = _room(client, pid)
    older = delivery_task(client, room)
    _seat(client, older, device="gone", lost=True)
    task = delivery_task(client, room, new=True)
    return pid, room, task


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
    pid, room, task = _channel_with_released_older_task(client)
    if seated:
        _seat(client, task, device=None)

    listing = _live(client, pid, room, task, "files")
    diff = _live(client, pid, room, task, "diff")

    assert listing.status_code == 200, listing.text
    assert listing.json()["data"]["data"] == []
    assert diff.status_code == 200, diff.text
    assert diff.json()["data"]["diff"] == ""
    assert asked == []


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


def test_a_failing_machine_is_not_reported_as_released(client, machines):
    pid, room, task = _channel_with_released_older_task(client)
    _seat(client, task, device="failing")

    response = _live(client, pid, room, task, "diff")

    assert response.status_code == 503, response.text
    message = response.json()["error"]["message"]
    assert "已释放" not in message
    assert "git: index.lock exists" in message and "已提交版本" in message


def test_a_task_whose_own_sandbox_was_released_says_so(client, machines):
    _, asked = machines
    pid, room, task = _channel_with_released_older_task(client)
    _seat(client, task, device="gone", lost=True)

    response = _live(client, pid, room, task, "files")

    assert response.status_code == 503, response.text
    assert "已释放" in response.json()["error"]["message"]
    assert GONE not in response.json()["error"]["message"]
    assert asked == []


def test_a_machine_of_an_earlier_room_generation_is_not_read(client, machines):
    _, asked = machines
    pid, room, task = _channel_with_released_older_task(client)
    _seat(client, task, device="working", generation=str(uuid.uuid4()))

    listing = _live(client, pid, room, task, "files")

    assert listing.status_code == 200, listing.text
    assert listing.json()["data"]["data"] == []
    assert asked == []
