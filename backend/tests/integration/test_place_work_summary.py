"""「现场」是房间的，因为跑活的会话只有房间那一个。

`/projects/{pid}/topics/{id}/work-summary` 说这一格该不该摆出来（`has_run`）。
它只答**房间**——一个房间一个会话，它派出去的每一个分身都住在里面。
拿一张卡的 id 去问，答的是 404：那不是一个地点。

`work-summary` 的 `changed_files` 属于**树**：一棵树 = 一个分支 = 一个 PR = 一批活，
所以它是这个房间当前这一批一起写出来的，不是谁一个人的。
"""

import asyncio
import uuid

from app.domain.agent_session.repositories import AgentSessionRepository
from app.domain.identity.handles import CHEESE_HANDLE
from tests.integration.conftest import post_project
from tests.integration.test_connector_viewer import _login
from tests.machine_work import declare_task, machine_commits


def _room(client) -> tuple[str, str]:
    pid = post_project(client, json={"name": "P", "owner_handle": "alice"}).json()[
        "data"
    ]["id"]
    rid = client.post(
        "/topics",
        json={"project_id": pid, "title": "房间", "created_by": "alice"},
    ).json()["data"]["id"]
    return pid, rid


def _thread(client, room_id: str, title: str = "一件活") -> str:
    r = client.post(
        f"/topics/{room_id}/split",
        json=dict(reviewer_handle="alice", **{"title": title}),
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["data"]["id"]
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
                topic_id=uuid.UUID(place_id),
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


def test_a_card_has_no_work_summary_of_its_own(client):
    """同一个理由的另一半：`has_run` 问的是「这个地点跑过没有」，而卡不是地点。"""
    pid, room = _room(client)
    card = _thread(client, room)

    assert _summary(client, pid, card).status_code == 404


def test_a_room_that_has_run_says_so(client):
    """前端用 `has_run` 决定要不要提供「现场」这一格。"""
    pid, room = _room(client)

    assert _summary(client, pid, room).json()["data"]["has_run"] is False
    _seed_session(client, room)
    assert _summary(client, pid, room).json()["data"]["has_run"] is True


def test_room_summary_combines_its_independent_tasks(client):
    """The room summary includes paths from every open task branch."""
    pid, room = _room(client)
    first = _thread(client, room)
    second = _thread(client, room)
    machine_commits(
        uuid.UUID(pid), uuid.UUID(first), {"first.txt": "First task\n"}, "First change"
    )
    machine_commits(
        uuid.UUID(pid),
        uuid.UUID(second),
        {"second.txt": "Second task\n"},
        "Second change",
    )
    assert _summary(client, pid, room).json()["data"]["changed_files"] == [
        "first.txt",
        "second.txt",
    ]
