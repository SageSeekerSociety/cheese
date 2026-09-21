"""The 文件 panel's HTTP contract for not destroying files.

The service-level guarantees are pinned in tests/unit/test_workspace_file_safety;
these check the wire the panel actually talks to — what a read hands the browser,
and that a save which lost a race comes back as a conflict rather than a 200.
"""

import asyncio
import hashlib
import subprocess
import uuid

import pytest

from app.domain.agent import execution
from app.domain.agent.harness.claude_code.remote_execution.runtime import Executor
from app.domain.topic.models import Topic
from tests.delivery import delivery_task_id

BINARY = bytes(range(256)) * 8


@pytest.fixture(autouse=True)
def task_machine(client, monkeypatch, tmp_path):
    executor = object.__new__(Executor)
    executor.env = {"HOME": str(tmp_path / "machine")}
    client.test_machine_home = tmp_path / "machine"

    async def call(target, method, params):
        assert target["kind"] == "device" and method == "task_fs"
        return executor.task_fs(params)

    monkeypatch.setattr(execution, "call", call)


def _worktree(client, topic):
    return (
        client.test_machine_home
        / ".cheese/tasks"
        / str(delivery_task_id(client, topic))
    )


def _mkproject(client) -> uuid.UUID:
    resp = client.post("/projects", json={"name": "P", "owner_handle": "alice"}).json()
    return uuid.UUID(resp["data"]["id"])


def _mktopic(client, pid: uuid.UUID) -> uuid.UUID:
    response = client.post("/topics", json={"project_id": str(pid), "title": "Files"})
    assert response.status_code == 200
    room_id = uuid.UUID(response.json()["data"]["id"])

    async def place():
        async with client.test_factory() as session:
            room = await session.get(Topic, room_id)
            from app.domain.agent_session.services import AgentSessionService

            await AgentSessionService(session).remember_place(
                topic_id=room_id,
                agent_handle="cheese",
                work_lease={"kind": "device"},
                runtime_location={
                    "device_id": "test-device",
                    "channel": "central",
                    "resource_id": str(room.resource_id or room.id),
                },
            )
            await session.commit()

    asyncio.run(place())
    return room_id


def _owner(client) -> dict[str, str]:
    from tests.integration.test_connector_viewer import _login

    return {"Authorization": f"Bearer {_login(client, 'alice')}"}


def _put(client, pid: uuid.UUID, topic: uuid.UUID, path: str, data: bytes) -> None:
    """Put a file into the topic's worktree the way a turn would."""
    target = _worktree(client, topic) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    root = _worktree(client, topic)
    if not (root / ".git").exists():
        subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    target.write_bytes(data)


def test_reading_a_binary_file_returns_no_text_to_edit(client):
    pid = _mkproject(client)
    tid = _mktopic(client, pid)
    _put(client, pid, tid, "app.bin", BINARY)

    body = client.get(
        f"/projects/{pid}/file",
        params={
            "path": "app.bin",
            "topic": str(tid),
            "task": str(delivery_task_id(client, tid)),
        },
        headers=_owner(client),
    ).json()["data"]

    assert body["binary"] is True
    assert body["content"] is None
    assert body["bytes"] == len(BINARY)


def test_saving_over_a_binary_file_is_rejected_and_the_bytes_survive(client):
    pid = _mkproject(client)
    tid = _mktopic(client, pid)
    _put(client, pid, tid, "app.bin", BINARY)
    before = hashlib.md5(BINARY).hexdigest()

    resp = client.put(
        f"/projects/{pid}/file",
        params={"topic": str(tid), "task": str(delivery_task_id(client, tid))},
        json={"path": "app.bin", "content": "文本"},
        headers=_owner(client),
    )

    assert resp.status_code >= 400
    after = (_worktree(client, tid) / "app.bin").read_bytes()
    assert hashlib.md5(after).hexdigest() == before


def test_a_save_that_lost_the_race_answers_409_and_changes_nothing(client):
    pid = _mkproject(client)
    tid = _mktopic(client, pid)
    _put(client, pid, tid, "note.txt", "原始内容\n".encode())
    headers = _owner(client)

    read = client.get(
        f"/projects/{pid}/file",
        params={
            "path": "note.txt",
            "topic": str(tid),
            "task": str(delivery_task_id(client, tid)),
        },
        headers=headers,
    ).json()["data"]

    # 芝士 writes the same file while the human is typing.
    _put(client, pid, tid, "note.txt", "芝士这一轮写的\n".encode())

    resp = client.put(
        f"/projects/{pid}/file",
        params={"topic": str(tid), "task": str(delivery_task_id(client, tid))},
        json={
            "path": "note.txt",
            "content": read["content"] + "人加的\n",
            "version": read["version"],
        },
        headers=headers,
    )

    assert resp.status_code == 409
    disk = (_worktree(client, tid) / "note.txt").read_text(encoding="utf-8")
    assert disk == "芝士这一轮写的\n"


def test_a_save_carrying_the_current_version_goes_through(client):
    pid = _mkproject(client)
    tid = _mktopic(client, pid)
    _put(client, pid, tid, "note.txt", "原始内容\n".encode())
    headers = _owner(client)

    read = client.get(
        f"/projects/{pid}/file",
        params={
            "path": "note.txt",
            "topic": str(tid),
            "task": str(delivery_task_id(client, tid)),
        },
        headers=headers,
    ).json()["data"]

    resp = client.put(
        f"/projects/{pid}/file",
        params={"topic": str(tid), "task": str(delivery_task_id(client, tid))},
        json={"path": "note.txt", "content": "人写的\n", "version": read["version"]},
        headers=headers,
    )

    assert resp.status_code == 200
    disk = (_worktree(client, tid) / "note.txt").read_text(encoding="utf-8")
    assert disk == "人写的\n"
    # The panel can keep saving without re-reading.
    assert resp.json()["data"]["version"]


def test_a_dangling_symlink_does_not_500_the_file_listing(client):
    pid = _mkproject(client)
    tid = _mktopic(client, pid)
    _put(client, pid, tid, "real.txt", b"ok\n")
    (_worktree(client, tid) / "dangling").symlink_to("/nonexistent/target")

    resp = client.get(
        f"/projects/{pid}/files",
        params={"topic": str(tid), "task": str(delivery_task_id(client, tid))},
        headers=_owner(client),
    )

    assert resp.status_code == 200
    assert "real.txt" in {f["path"] for f in resp.json()["data"]["data"]}
