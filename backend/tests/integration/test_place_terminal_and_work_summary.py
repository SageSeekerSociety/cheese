"""一条支线也要能打开自己的「现场」。

「现场」是两半：`/topics/{id}/terminal` 说有没有一块活着的屏幕可看，
`/projects/{pid}/topics/{id}/work-summary` 说这一格该不该摆出来（`has_run`）。
两条都以 `get_or_404` 开头 —— 那个函数只会找 `topics` 表里的一行，所以传一条支线
的 id 进去一律 404，而它问的那块屏幕从头到尾就开在这个 id 底下（`device_provider`
开屏幕时传的 `topic_id` 就是地点 id）。

`work-summary` 的两个字段**不同粒度**，这也是它们分开算的全部理由：

- `changed_files` 属于**树**。一棵树 = 一个分支 = 一个 PR = 一批活，同一批支线写
  的是同一条分支，那份 diff 诚实地说就是他们一起做的。
- `has_run` 属于**地点**。支线跑的是自己的 agent、自己的会话、自己那块屏幕。

两行相邻、长得几乎一样，最容易在重构里被写反，所以两个口径各钉一次。
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
    """把这个地点标成「跑过」—— 真实第一轮做的就是这件事。

    `AgentSessionRepository` 自己把一个 id 解析成 (房间, 支线) 两半，所以这里传
    支线的 id 就只标这条支线。
    """

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


def test_a_threads_terminal_is_answered_instead_of_404(client, monkeypatch):
    """点开一条支线，「现场」问的第一句话不能是 404。

    屏幕就开在这个 id 底下；挡路的只是一个只会查 `topics` 表的守卫。
    """
    monkeypatch.setattr(terminal, "_device_screen_id", _screen_open_for([], "s-7"))
    _pid, room = _room(client)
    thread = _thread(client, room)

    r = client.get(f"/topics/{thread}/terminal", headers=_owner(client))

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["available"] is True, data
    assert data["ws"] == "/connector/session/s-7/screen"


def test_a_threads_pane_is_looked_up_by_the_thread_not_by_its_room(
    client, monkeypatch
):
    """按房间去找，找到的是同伴那块屏幕 —— 比 404 难发现得多。

    房间和支线的 id 都是 uuid，传错一个不会报错，只会安静地看别人干活。
    """
    seen: list[uuid.UUID] = []
    monkeypatch.setattr(terminal, "_device_screen_id", _screen_open_for(seen, "s-9"))
    _pid, room = _room(client)
    thread = _thread(client, room)

    client.get(f"/topics/{thread}/terminal", headers=_owner(client))

    assert seen == [uuid.UUID(thread)], seen


def test_a_thread_with_no_pane_open_says_unavailable_not_404(client, monkeypatch):
    """没在跑的支线要答「没有」，答成 404 的话前端分不清「不在跑」和「不存在」。"""
    monkeypatch.setattr(terminal, "_device_screen_id", lambda _t: None)
    _pid, room = _room(client)
    thread = _thread(client, room)

    r = client.get(f"/topics/{thread}/terminal", headers=_owner(client))

    assert r.status_code == 200, r.text
    assert r.json()["data"]["available"] is False


def test_a_threads_pane_is_refused_to_someone_outside_the_room(client, monkeypatch):
    """名册只有房间那一份。支线没有自己的名册，所以进不了房间的人也看不了它的活。"""
    monkeypatch.setattr(terminal, "_device_screen_id", _screen_open_for([], "s-3"))
    _pid, room = _room(client)
    thread = _thread(client, room)

    data = client.get(
        f"/topics/{thread}/terminal", headers=_stranger(client)
    ).json()["data"]

    assert data["available"] is False, data
    assert "ws" not in data


def test_a_rooms_terminal_is_unchanged(client, monkeypatch):
    """回归：房间那一侧一个字没变 —— 房间的 id 既是地点也是房间，两个问题同一个答案。"""
    monkeypatch.setattr(terminal, "_device_screen_id", _screen_open_for([], "s-1"))
    _pid, room = _room(client)

    data = client.get(f"/topics/{room}/terminal", headers=_owner(client)).json()["data"]

    assert data["available"] is True
    assert data["ws"] == "/connector/session/s-1/screen"


# --- 这一格该不该摆出来 ------------------------------------------------------


def test_a_thread_answers_the_work_summary_at_all(client):
    """前端用 `has_run` 决定要不要提供「现场」这一格。它 404，格子就永远不出现。"""
    pid, room = _room(client)
    thread = _thread(client, room)

    r = _summary(client, pid, thread)

    assert r.status_code == 200, r.text
    assert set(r.json()["data"]) == {"changed_files", "has_run"}


def test_has_run_is_the_threads_own_not_its_rooms(client):
    """房间跑过，不等于它派出去的活跑过。

    回落成房间的话，每一条刚派出去、一步还没走的活都会摆出一个空的「现场」。
    """
    pid, room = _room(client)
    thread = _thread(client, room)
    _seed_session(client, room)

    assert _summary(client, pid, room).json()["data"]["has_run"] is True
    assert _summary(client, pid, thread).json()["data"]["has_run"] is False


def test_two_threads_in_one_room_do_not_share_has_run(client):
    """反过来的那一半：一条跑过的活不能替它没跑过的同伴回答。"""
    pid, room = _room(client)
    ran = _thread(client, room, title="跑过的")
    idle = _thread(client, room, title="没跑过的")
    _seed_session(client, ran)

    assert _summary(client, pid, ran).json()["data"]["has_run"] is True
    assert _summary(client, pid, idle).json()["data"]["has_run"] is False


def test_a_threads_changed_files_stay_the_trees(client):
    """`changed_files` 是**树**的，不跟着 `has_run` 一起改成按地点算。

    一批活共用一条分支，diff 本来就是共享的；把它切成「这条活改的」是发明一个不
    存在的隔离，而看的人会以为那些改动是这条活一个人做的。
    """
    pid, room = _room(client)
    thread = _thread(client, room)
    machine_commits(
        uuid.UUID(pid), uuid.UUID(room), {"shared.txt": "一批活一起写的\n"}, "一笔改动"
    )

    room_files = _summary(client, pid, room).json()["data"]["changed_files"]
    thread_files = _summary(client, pid, thread).json()["data"]["changed_files"]

    assert room_files == ["shared.txt"], room_files
    assert thread_files == room_files
