"""侧栏那棵树的数据源：项目里的每一件活，各带它骑的那张卡。

一件活以前在界面上等于不存在——它只在被派出去那一刻、房间时间线上一条标记里出现
过一次。要把「房间 → 它派出去的活 → 那件活的 PR」画出来，前端需要一次问全，而不是
一个房间一个请求（这个项目有一百七十多个房间）再一条活一个请求去拿卡。

所以这里钉两件事：**一次问全**，以及**卡是那条支线自己的**——一个房间里两条活各有
各的卡，串了的话侧栏会把 A 的 PR 挂到 B 头上，而那正是没人会去核对的一种错。
"""

from tests.integration.conftest import session_auth_headers


def _project(client) -> str:
    return client.post("/projects", json={"name": "P", "owner_handle": "alice"}).json()[
        "data"
    ]["id"]


def _room(client, project_id: str, title: str) -> str:
    return client.post(
        "/topics",
        json={"project_id": project_id, "title": title, "created_by": "alice"},
    ).json()["data"]["id"]


def _thread(client, room_id: str, title: str) -> str:
    r = client.post(f"/topics/{room_id}/split", json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _file_card(client, place_id: str, subject: str) -> str:
    r = client.post(
        f"/topics/{place_id}/accept-card",
        json={
            "change_subject": subject,
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _tasks(client, project_id: str) -> list[dict]:
    r = client.get(f"/projects/{project_id}/tasks")
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def test_every_thread_in_the_project_comes_back_at_once(client):
    """两个房间各派一件活，一个请求全拿到——树是一次画出来的。"""
    pid = _project(client)
    room_a = _room(client, pid, "运维")
    room_b = _room(client, pid, "前端")
    _thread(client, room_a, "查一下分页接口")
    _thread(client, room_b, "改一下侧栏")

    rows = _tasks(client, pid)
    assert {r["title"] for r in rows} == {"查一下分页接口", "改一下侧栏"}
    # 每一行都说得出自己挂在哪个房间——树就是靠这条边建出来的。
    assert {r["room_id"] for r in rows} == {room_a, room_b}


def test_a_thread_carries_the_card_it_rides_on(client):
    """侧栏上「交付到哪一步了」读的就是这个字段。"""
    pid = _project(client)
    room = _room(client, pid, "运维")
    thread = _thread(client, room, "查一下分页接口")
    _file_card(client, thread, "fix(api): return the last row of a page")

    (row,) = _tasks(client, pid)
    assert row["card"] is not None
    assert row["card"]["status"] == "pending"


def test_a_thread_with_nothing_filed_says_so_rather_than_guessing(client):
    """大多数活在做的过程中都没有卡。空就是空，不编一个状态出来。"""
    pid = _project(client)
    room = _room(client, pid, "运维")
    _thread(client, room, "还在做")

    (row,) = _tasks(client, pid)
    assert row["card"] is None


def test_one_threads_card_never_lands_on_another(client):
    """同一个房间里两条活各有各的卡。串了的话侧栏会把 A 的 PR 挂到 B 头上——
    屏幕上看着完全正常，没人会去核对。"""
    pid = _project(client)
    room = _room(client, pid, "运维")
    filed = _thread(client, room, "已经递卡的")
    _thread(client, room, "还没递卡的")
    _file_card(client, filed, "fix(api): one")

    by_title = {r["title"]: r for r in _tasks(client, pid)}
    assert by_title["已经递卡的"]["card"] is not None
    assert by_title["还没递卡的"]["card"] is None


def test_the_rooms_own_card_is_not_a_threads(client):
    """房间自己递的卡是**整条分支**的交付，不挂在任何一件活头上。"""
    pid = _project(client)
    room = _room(client, pid, "运维")
    _thread(client, room, "一件活")
    _file_card(client, room, "chore: the room's own delivery")

    (row,) = _tasks(client, pid)
    assert row["card"] is None


def test_the_newest_card_is_the_one_shown(client):
    """一条活被打回之后会再递一次。侧栏要说的是它**现在**在哪一步。"""
    pid = _project(client)
    room = _room(client, pid, "运维")
    thread = _thread(client, room, "改了两版的")
    first = _file_card(client, thread, "fix(api): first try")
    r = client.post(
        f"/accept-cards/{first}/reject",
        json={"decided_by": "alice", "reason": "再想想"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    second = _file_card(client, thread, "fix(api): second try")

    (row,) = _tasks(client, pid)
    assert row["card"]["id"] == second
