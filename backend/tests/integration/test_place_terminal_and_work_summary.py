"""「现场」是房间的，因为跑活的会话只有房间那一个。

「现场」是两半：`/topics/{id}/terminal` 说有没有一块活着的屏幕可看，
`/projects/{pid}/topics/{id}/work-summary` 说这一格该不该摆出来（`has_run`）。
两条都只答**房间**——一个房间一块屏幕、一个会话，它派出去的每一个分身都住在里面。
拿一张卡的 id 去问，答的是 404：那不是一个地点。

`work-summary` 的 `changed_files` 属于**树**：一棵树 = 一个分支 = 一个 PR = 一批活，
所以它是这个房间当前这一批一起写出来的，不是谁一个人的。
"""

import asyncio
import uuid

from app.api.routes import terminal
from app.domain.agent_session.repositories import AgentSessionRepository
from app.domain.identity.handles import CHEESE_HANDLE
from tests.integration.test_connector_viewer import _login
from tests.machine_work import machine_commits


def _room(client) -> tuple[str, str]:
    pid = client.post("/projects", json={"name": "P", "owner_handle": "alice"}).json()[
        "data"
    ]["id"]
    rid = client.post(
        "/topics",
        json={"project_id": pid, "title": "房间", "created_by": "alice"},
    ).json()["data"]["id"]
    return pid, rid


def _thread(client, room_id: str, title: str = "一件活") -> str:
    r = client.post(f"/topics/{room_id}/split", json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _owner(client) -> dict[str, str]:
    return {"Authorization": f"Bearer {_login(client, 'alice')}"}


def _stranger(client) -> dict[str, str]:
    return {"Authorization": f"Bearer {_login(client, 'mallory')}"}


def _screen_open_for(seen: list[uuid.UUID], sid: str):
    """Stand in for the hub, and record WHICH id the route looked the pane up by."""

    def _resolve(topic_id: uuid.UUID) -> str | None:
        seen.append(topic_id)
        return sid

    return _resolve


def _seed_session(client, place_id: str) -> None:
    """把这个房间标成「跑过」—— 真实第一轮做的就是这件事。"""

    async def _run() -> None:
        async with client.test_factory() as s:
            await AgentSessionRepository(s).save(
                topic_id=uuid.UUID(place_id),
                agent_handle=CHEESE_HANDLE,
                resume_token="sess-" + uuid.uuid4().hex[:8],
            )
            await s.commit()

    asyncio.run(_run())


def _summary(client, pid: str, place_id: str):
    return client.get(
        f"/projects/{pid}/topics/{place_id}/work-summary", headers=_owner(client)
    )


# --- 现场：有没有一块屏幕可看 ------------------------------------------------


def test_a_card_has_no_terminal_of_its_own(client, monkeypatch):
    """一张卡问不出屏幕来 —— 屏幕开在房间底下，做这条活的分身就跑在那里面。

    404 不是一条为它加的拒绝，是「这个 id 名下没有地点」的自然结果。
    """
    monkeypatch.setattr(terminal, "_device_screen_id", _screen_open_for([], "s-7"))
    _pid, room = _room(client)
    card = _thread(client, room)

    assert (
        client.get(f"/topics/{card}/terminal", headers=_owner(client)).status_code
        == 404
    )


def test_a_rooms_pane_is_looked_up_by_the_room(client, monkeypatch):
    seen: list[uuid.UUID] = []
    monkeypatch.setattr(terminal, "_device_screen_id", _screen_open_for(seen, "s-2"))
    _pid, room = _room(client)

    client.get(f"/topics/{room}/terminal", headers=_owner(client))

    assert seen == [uuid.UUID(room)]


def test_a_rooms_pane_is_refused_to_someone_outside_it(client, monkeypatch):
    monkeypatch.setattr(terminal, "_device_screen_id", _screen_open_for([], "s-3"))
    _pid, room = _room(client)

    data = client.get(f"/topics/{room}/terminal", headers=_stranger(client)).json()[
        "data"
    ]

    assert data["available"] is False, data


def test_a_rooms_terminal_is_unchanged(client, monkeypatch):
    """回归：房间那一侧一个字没变 —— 房间的 id 既是地点也是房间，两个问题同一个答案。"""
    monkeypatch.setattr(terminal, "_device_screen_id", _screen_open_for([], "s-1"))
    _pid, room = _room(client)

    data = client.get(f"/topics/{room}/terminal", headers=_owner(client)).json()["data"]

    assert data["available"] is True
    assert data["ws"] == "/connector/session/s-1/screen"


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


def test_changed_files_are_the_trees_not_one_workers(client):
    """`changed_files` 是**树**的。一批活共用一条分支，diff 本来就是共享的；把它
    切成「这条活改的」是发明一个不存在的隔离。
    """
    pid, room = _room(client)
    _thread(client, room)
    machine_commits(
        uuid.UUID(pid), uuid.UUID(room), {"shared.txt": "一批活一起写的\n"}, "一笔改动"
    )

    assert _summary(client, pid, room).json()["data"]["changed_files"] == ["shared.txt"]
