"""「编辑」 in the library's 项目文件: reading a project file needs no task; the
moment a person edits one, the platform opens a task for them.

Rules: the task is the presser's, in the project's 综合, named after the file,
and its AI teammate is not summoned. Nothing is said in 综合 and no list shows
the task until it changes something; then 综合 is told once. A task opened
this way and left unchanged is not left behind: no list shows it, and the
person's next 「编辑」 opens it again instead of a second empty task.
"""

import asyncio
import subprocess
import uuid

from sqlalchemy import select

from app.domain.agent.harness import harness_for
from app.domain.block.models import Block
from app.domain.project.models import Project
from app.domain.topic.models import Topic
from tests.integration.test_file_panel_safety import (  # noqa: F401 — fixture
    _mkproject,
    _owner,
    task_machine,
)
from tests.support.environments import remember_place


def _root(client, pid: uuid.UUID) -> uuid.UUID:
    async def read():
        async with client.test_factory() as session:
            return (await session.get(Project, pid)).root_topic_id

    return asyncio.run(read())


def _edit(client, pid, path, headers):
    resp = client.post(
        f"/projects/{pid}/files/edit", json={"path": path}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _blocks(client, conversation_id) -> list[Block]:
    async def read():
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(Block).where(Block.conversation_id == conversation_id)
            )
            return list(rows)

    return asyncio.run(read())


def _openings(client, room_id, task_id) -> list[Block]:
    """The lines in the room saying this task was opened."""
    return [
        b
        for b in _blocks(client, room_id)
        if (b.meta or {}).get("action") == "task_created"
        and (b.meta or {}).get("task_id") == str(task_id)
    ]


def _listed(client, pid, root, task_id, headers) -> bool:
    project = client.get(
        f"/projects/{pid}/tasks", params={"status": "open"}, headers=headers
    )
    room = client.get(f"/topics/{root}/tasks", params={"limit": 0}, headers=headers)
    assert project.status_code == 200 and room.status_code == 200
    return str(task_id) in project.text or str(task_id) in room.text


def _on_a_machine(client, root, task_id, files: dict[str, str]):
    """The task's copy is on a machine, holding these files."""
    home = client.test_machine_home / ".cheese/tasks" / str(task_id)
    home.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(home)], check=True, capture_output=True)
    for path, text in files.items():
        (home / path).write_text(text, encoding="utf-8")

    async def place():
        async with client.test_factory() as session:
            room = await session.get(Topic, root)
            await remember_place(
                session,
                conversation_id=task_id,
                agent_handle="cheese",
                environment={"kind": "device"},
                runtime_location={
                    "device_id": "test-device",
                    "channel": "central",
                    "resource_id": str(room.resource_id or room.id),
                },
                harness=harness_for(None),
            )
            await session.commit()

    asyncio.run(place())


def _save(client, pid, root, task_id, headers, content):
    params = {"path": "stack.c", "topic": str(root), "task": str(task_id)}
    read = client.get(f"/projects/{pid}/file", params=params, headers=headers).json()[
        "data"
    ]
    resp = client.put(
        f"/projects/{pid}/file",
        params={"topic": str(root), "task": str(task_id)},
        json={
            "path": "stack.c",
            "content": content,
            "version": read["version"],
            "base": read["content"],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


def test_edit_opens_a_task_of_the_presser_in_general_that_nobody_is_told_of(client):
    pid = _mkproject(client)
    root = _root(client, pid)
    headers = _owner(client)

    task = _edit(client, pid, "src/stack.c", headers)

    assert task["room_id"] == str(root)
    assert task["owner_handle"] == "alice"
    assert task["title"] == "stack.c"
    assert task["status"] == "open"
    # The teammate is not summoned: nothing is said in the task.
    assert _blocks(client, uuid.UUID(task["id"])) == []
    # Nothing is said in 综合, and no list shows it.
    assert _openings(client, root, task["id"]) == []
    assert not _listed(client, pid, root, task["id"], headers)


def test_an_unchanged_task_is_not_left_behind_the_next_edit_opens_it_again(client):
    pid = _mkproject(client)
    root = _root(client, pid)
    headers = _owner(client)

    first = _edit(client, pid, "stack.c", headers)
    again = _edit(client, pid, "queue.c", headers)

    assert again["id"] == first["id"]
    assert again["title"] == "queue.c"
    assert not _listed(client, pid, root, first["id"], headers)


def test_the_first_change_tells_general_once_and_the_task_is_listed(client):
    pid = _mkproject(client)
    root = _root(client, pid)
    headers = _owner(client)
    task = _edit(client, pid, "stack.c", headers)
    task_id = uuid.UUID(task["id"])
    _on_a_machine(client, root, task_id, {"stack.c": "int top;\n"})

    _save(client, pid, root, task_id, headers, "int top = -1;\n")
    _save(client, pid, root, task_id, headers, "int top = 0;\n")

    (line,) = _openings(client, root, task_id)
    assert "stack.c" in line.content
    assert _listed(client, pid, root, task_id, headers)
    # Changed, it is kept: the next 「编辑」 opens another task.
    assert _edit(client, pid, "stack.c", headers)["id"] != task["id"]
