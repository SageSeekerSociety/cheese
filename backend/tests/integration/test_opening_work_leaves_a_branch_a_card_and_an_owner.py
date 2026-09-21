"""开一条活留下的是一个分支、一张卡和一个负责人 —— 别的什么都不多（结论 31）。

`cheese split` 从前还留下两样：这条活自己的那条会话，和它自己那份地点租约。做这
条活的是房间会话里的一个原生子 agent，用的是父进程那双手（结论 43），所以那两样
是多出来的 —— 多一条会话就是多一段要恢复、要回收、要算钱的对话，多一份租约就是
多一台没人记得还的机器。

这里钉的是**平台侧多出来的东西**：开两条活，多的是两张卡和两条分支，`agent_sessions`
不多行、租约不多份。数的是行，不是某个函数有没有被调到 —— 会话和租约是谁写的将来
还会变，而「开活之后这两张表长了没有」这个问题对哪条路径都成立。
"""

import uuid

from sqlalchemy import func, select

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


def test_opening_two_pieces_of_work_leaves_two_cards_and_two_branches(client):
    """两条活 = 两张卡 + 两条分支 + 两个负责人，一张卡一条分支，不共用。"""
    _project_id, room_id = _room_with_a_session_on_a_machine(client)

    first = _open_work(client, room_id, "接口分页")
    second = _open_work(client, room_id, "补索引")

    cards = client.get(f"/topics/{room_id}/tasks").json()["data"]["data"]
    assert [card["id"] for card in cards] == [first["id"], second["id"]]
    assert first["branch_name"] and second["branch_name"]
    assert first["branch_name"] != second["branch_name"]
    assert first["owner_handle"] == second["owner_handle"] == "alice"


def test_opening_work_adds_no_session_of_its_own_and_no_second_lease(client):
    """开活不多一条会话，也不多一份租约。"""
    _project_id, room_id = _room_with_a_session_on_a_machine(client)
    before_rows, before_leases = _sessions_and_leases(client)

    _open_work(client, room_id, "接口分页")
    _open_work(client, room_id, "补索引")

    after_rows, after_leases = _sessions_and_leases(client)
    assert after_rows == before_rows == 1
    assert after_leases == before_leases == [_LEASE]
