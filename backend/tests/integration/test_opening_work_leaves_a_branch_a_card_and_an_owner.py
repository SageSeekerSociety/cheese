"""开一条活留下的是一个分支、一张卡和一个负责人 —— 别的什么都不多（结论 31）。

`cheese_task` 从前还留下两样：这条活自己的那条会话，和它自己那份地点租约。做这
条活的是房间会话里的一个原生子 agent，用的是父进程那双手（结论 43），所以那两样
是多出来的 —— 多一条会话就是多一段要恢复、要回收、要算钱的对话，多一份租约就是
多一台没人记得还的机器。

这里钉的是**平台侧多出来的东西**：开两条活，多的是两张卡和两条分支，`agent_sessions`
不多行、租约不多份。数的是行，不是某个函数有没有被调到 —— 会话和租约是谁写的将来
还会变，而「开活之后这两张表长了没有」这个问题对哪条路径都成立。
"""

import uuid

from sqlalchemy import func, select

from app.domain.agent.harness import deployment_harness
from app.domain.agent_session.models import AgentSession

_LEASE = {
    "kind": "device",
    "device_id": "machine-1",
    "home": "/home/cheese",
    "state": "/home/cheese/.cheese/executor",
}


def _room_with_a_session_on_a_machine(client) -> tuple[str, str]:
    """一个房间，里面坐着一条已经租到手的会话。

    先让它有一条，后面「不多行、不多份」才说得出话 —— 从零行开始的话，写侧坏成
    什么样这条测试都是绿的。
    """
    project = client.post("/projects", json={"name": "P", "owner_handle": "alice"})
    project_id = project.json()["data"]["id"]
    room_id = client.post(
        "/topics",
        json={"project_id": project_id, "title": "房间", "created_by": "alice"},
    ).json()["data"]["id"]

    async def _seed() -> None:
        async with client.test_factory() as session:
            session.add(
                AgentSession(
                    topic_id=uuid.UUID(room_id),
                    agent_handle="cheese",
                    # 同一个骨架，不然这条会话和开活时写的那条各占一行，
                    # 「不多行」就变成了「多了一行，但那一行本来就该多」。
                    harness=deployment_harness(),
                    runtime_location={
                        "device_id": "machine-1",
                        "channel": "device",
                        "resource_id": room_id,
                    },
                    work_lease=dict(_LEASE),
                )
            )
            await session.commit()

    client.portal.call(_seed)
    return project_id, room_id


def _sessions_and_leases(client) -> tuple[int, list[dict]]:
    got: dict[str, object] = {}

    async def _read() -> None:
        async with client.test_factory() as session:
            got["rows"] = await session.scalar(
                select(func.count()).select_from(AgentSession)
            )
            got["leases"] = [
                lease
                for lease in await session.scalars(select(AgentSession.work_lease))
                if lease is not None
            ]

    client.portal.call(_read)
    return int(got["rows"]), list(got["leases"])  # type: ignore[arg-type]


def _open_work(client, room_id: str, title: str) -> dict:
    response = client.post(
        f"/topics/{room_id}/split",
        json={"title": title, "reviewer_handle": "alice", "created_by": "alice"},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_opening_two_pieces_of_work_leaves_two_flat_cards_each_on_its_own_branch(
    client,
):
    """两条活 = 两张卡 + 两条分支 + 两个负责人，而且两条活是平的。

    分支名长什么样不在这里验：平台只记一个名字，建分支的是机器上的执行器
    （`room_task/services.py` 的 `task.branch_name = f"task/{...}"`）。这里验的是
    P28 验收那句话本身 —— 两条活各有一条自己的分支，名字非空且互不相等，哪天分支
    名改成从别的东西算出来、两条活撞到同一个名字上，这里会红。还有这两条活**从
    哪里开出去**：结论 33 说活在房间里是平的，没有子卡，所以两条都该从同一条基线
    长出来、谁也不挂在谁身上 —— 哪天 split 开始默认把新活接在上一条后面，这里会红。
    """
    _project_id, room_id = _room_with_a_session_on_a_machine(client)

    first = _open_work(client, room_id, "接口分页")
    second = _open_work(client, room_id, "补索引")

    cards = client.get(f"/topics/{room_id}/tasks").json()["data"]["data"]
    assert [card["id"] for card in cards] == [first["id"], second["id"]]
    assert first["owner_handle"] == second["owner_handle"] == "alice"
    assert first["branch_name"] and second["branch_name"]
    assert first["branch_name"] != second["branch_name"]
    assert first["base_task_id"] is second["base_task_id"] is None
    assert first["base_branch"] == second["base_branch"]
    assert first["base_branch"] not in (first["branch_name"], second["branch_name"])


def test_opening_work_adds_no_session_of_its_own_and_no_second_lease(client):
    """开活不多一条会话，也不多一份租约。"""
    _project_id, room_id = _room_with_a_session_on_a_machine(client)
    before_rows, before_leases = _sessions_and_leases(client)

    _open_work(client, room_id, "接口分页")
    _open_work(client, room_id, "补索引")

    after_rows, after_leases = _sessions_and_leases(client)
    assert after_rows == before_rows == 1
    assert after_leases == before_leases == [_LEASE]
