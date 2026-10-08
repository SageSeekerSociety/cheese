"""工作面板的摘要：哪几格该摆出来，「改动」上挂什么数。

`/projects/{pid}/topics/{id}/work-summary` 说「现场」这一格该不该摆出来（`has_run`），
频道和任务各答各的。

`changed_files` 是一件任务自己分支上的改动，「改动」页签上的数字就是它。频道没有
「改动」页签（改动属于任务，在任务页看，#2422），所以频道答的是空的。
"""

import asyncio
import uuid

from app.domain.agent_session.repositories import AgentSessionRepository
from app.domain.identity.handles import CHEESE_HANDLE
from tests.integration.conftest import open_task, post_project, session_auth_headers
from tests.integration.test_connector_viewer import _login
from tests.machine_work import declare_task, machine_commits


def _room(client) -> tuple[str, str]:
    pid = post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    rid = client.post(
        "/topics",
        json={"project_id": pid, "title": "房间"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    return pid, rid


def _task(client, room_id: str, title: str = "一件活") -> str:
    task_id = open_task(client, room_id, title)["id"]
    room = client.get(f"/topics/{room_id}").json()["data"]
    declare_task(uuid.UUID(room["project_id"]), uuid.UUID(task_id))
    return task_id


def _owner(client) -> dict[str, str]:
    return {"Authorization": f"Bearer {_login(client, 'alice')}"}


def _seed_session(client, place_id: str) -> None:
    """把这个房间标成「跑过」—— 真实第一轮做的就是这件事。"""

    async def _run() -> None:
        async with client.test_factory() as s:
            await AgentSessionRepository(s).save(
                conversation_id=uuid.UUID(place_id),
                agent_handle=CHEESE_HANDLE,
                resume_token="sess-" + uuid.uuid4().hex[:8],
                harness="claude-code",
            )
            await s.commit()

    asyncio.run(_run())


def _summary(client, pid: str, place_id: str):
    return client.get(
        f"/projects/{pid}/topics/{place_id}/work-summary", headers=_owner(client)
    )


# --- 这一格该不该摆出来 ------------------------------------------------------


def test_a_task_has_a_work_summary_of_its_own(client):
    """任务是一段自己的对话：拿任务的 id 问，答的是这件活，不是它所在的房间。"""
    pid, room = _room(client)
    task = _task(client, room)

    r = _summary(client, pid, task)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["has_run"] is False


def test_a_room_that_has_run_says_so(client):
    """前端用 `has_run` 决定要不要提供「现场」这一格。"""
    pid, room = _room(client)

    assert _summary(client, pid, room).json()["data"]["has_run"] is False
    _seed_session(client, room)
    assert _summary(client, pid, room).json()["data"]["has_run"] is True


def test_changes_are_counted_on_each_task_and_never_on_the_channel(client):
    """每件任务数它自己分支上的改动；频道一件也不数。

    频道上没有「改动」页签可以挂这个数，而把频道里每件开着的任务都拿去比一遍，
    十几件任务就超过上限，频道页每次打开都是 503。"""
    pid, room = _room(client)
    first = _task(client, room)
    second = _task(client, room)
    machine_commits(
        uuid.UUID(pid), uuid.UUID(first), {"first.txt": "First task\n"}, "First change"
    )
    machine_commits(
        uuid.UUID(pid),
        uuid.UUID(second),
        {"second.txt": "Second task\n"},
        "Second change",
    )
    assert _summary(client, pid, first).json()["data"]["changed_files"] == ["first.txt"]
    assert _summary(client, pid, second).json()["data"]["changed_files"] == [
        "second.txt"
    ]
    assert _summary(client, pid, room).json()["data"]["changed_files"] == []
