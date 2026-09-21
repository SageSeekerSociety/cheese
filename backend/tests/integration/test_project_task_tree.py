"""侧栏那棵树的数据源：项目里的每一件活，各带它骑的那张卡。

一件活以前在界面上等于不存在——它只在被派出去那一刻、房间时间线上一条标记里出现
过一次。要把「房间 → 它派出去的活 → 那件活的 PR」画出来，前端需要一次问全，而不是
一个房间一个请求（这个项目有一百七十多个房间）再一条活一个请求去拿卡。

所以这里钉的是**一次问全**：两个房间各派一件活，一个请求把整棵树拿回来，每一行说得出
自己挂在哪个房间。

卡这一栏钉的是它**不许瞎猜**：递卡是房间的事（房间封树开 PR，一批活出一个 PR），支线
递不了，所以支线那一行没有卡就是没有卡，不能把房间那张挂上去充数。
"""

from tests.delivery import delivery_headers, delivery_task_id


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
    r = client.post(
        f"/topics/{room_id}/split",
        json=dict(reviewer_handle="alice", **{"title": title}),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _post_card(client, place_id: str, subject: str):
    return client.post(
        f"/topics/{place_id}/tasks/{delivery_task_id(client, place_id)}/accept-card",
        headers=delivery_headers(client, place_id),
        json={
            "change_subject": subject,
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
        },
    )


def _file_card(client, place_id: str, subject: str) -> str:
    r = _post_card(client, place_id, subject)
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


def test_a_card_cannot_file_so_its_row_never_grows_a_card(client):
    """一张卡递不出验收卡，所以它那一行永远是没有卡的。

    递卡=封树开 PR，那是房间对**一批**活说的话。一件活替兄弟们说了，PR 就带着
    它们没做完的东西飞出去了。现在这条规矩由地址空间保证：卡不是地点。
    """
    pid = _project(client)
    room = _room(client, pid, "运维")
    thread = _thread(client, room, "查一下分页接口")

    r = _post_card(client, thread, "fix(api): return the last row of a page")
    assert r.status_code == 404, r.text

    (row,) = _tasks(client, pid)
    assert row["card"] is None


def test_a_thread_with_nothing_filed_says_so_rather_than_guessing(client):
    """大多数活在做的过程中都没有卡。空就是空，不编一个状态出来。"""
    pid = _project(client)
    room = _room(client, pid, "运维")
    _thread(client, room, "还在做")

    (row,) = _tasks(client, pid)
    assert row["card"] is None


def test_a_delivery_card_belongs_only_to_its_own_task(client):
    """A sibling's pending card must not appear on unfinished work."""
    pid = _project(client)
    room = _room(client, pid, "运维")
    unfinished = _thread(client, room, "一件活")
    card = _file_card(client, room, "chore: independent delivery")
    rows = {row["id"]: row for row in _tasks(client, pid)}
    assert len(rows) == 2
    assert rows[unfinished]["card"] is None
    assert rows[str(delivery_task_id(client, room))]["card"]["id"] == card
