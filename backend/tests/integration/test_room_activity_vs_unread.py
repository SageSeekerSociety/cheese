"""房间「活着」和「有我要读的东西」是两个问题，答案相反。

两个查询挨在一起，join 的是同一对表，写法几乎一样，结论必须相反：

- **最后活动时间**要算上任务。一个房间的活正在做，这个房间就是活的，它该往列表
  上排——而「活都在任务里做、房间主线安静着」恰恰是这种房间的常态。
- **未读角标**不能算任务。每条任务里说一句话就把房间标成未读，红点会立刻变成噪音，
  而任务里的话本来只在负责人和 AI 队友之间。

写成一个文件是因为它们最可能的坏法是「顺手统一」：下一个人看到两处相似的查询、
一处带 `task_id IS NULL` 一处不带，很容易以为是漏了。
"""

from tests.conftest import wait_work_idle
from tests.integration.conftest import (
    chat_ws_url,
    join_project_team,
    open_task,
    post_message,
    post_project,
    session_auth_headers,
)


def _project(client) -> dict:
    return post_project(client, json={"name": "P"}, owner="alice").json()["data"]


def _room(client, project_id: str) -> str:
    return client.post(
        "/topics",
        json={"project_id": project_id, "title": "房间"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]


def _join(client, room_id: str, handle: str) -> None:
    """A task is reached through the ROOM's roster — there is no separate one
    per task — so its owner has to be in it, and a room seats only people who
    are in the project."""
    pid = client.get(f"/topics/{room_id}").json()["data"]["project_id"]
    join_project_team(client, pid, handle)
    r = client.post(
        f"/topics/{room_id}/members",
        json={"handle": handle, "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text


def _task_of(client, room_id: str, owner: str) -> str:
    return open_task(client, room_id, "一件活", owner=owner, start=False)["id"]


def _say_in_task(client, room_id: str, task_id: str, who: str, text: str) -> None:
    """负责人在任务里说话 —— 走它所在房间的地址，任务没有自己的。

    和房间主线上说话走的是两条路，但落的都是 `kind=message` 的块，这正是这个文件
    要比的东西：只有消息会被计进未读。
    """
    r = client.post(
        f"/topics/{room_id}/tasks/{task_id}/messages",
        json={"content": text},
        headers=session_auth_headers(who),
    )
    assert r.status_code == 200, r.text
    wait_work_idle()


def _say(client, place_id: str, who: str, text: str) -> None:
    """Says it the way a person does — the chat socket, a `kind=message` block.

    Both halves below have to travel the same way, or the badge half proves
    nothing: only messages are ever counted as unread, so saying it as, say, a
    doc comment would read as "not unread" for a reason that has nothing to do
    with tasks.
    """
    with client.websocket_connect(chat_ws_url(place_id, who)) as ws:
        post_message(client, place_id, who, {"content": text})
        while True:
            frame = ws.receive_json()
            if frame["type"] in ("done", "error"):
                assert frame["type"] == "done", frame
                break


def _rooms(client, project_id: str, viewer: str) -> dict[str, dict]:
    rows = client.get(
        f"/topics?project_id={project_id}", headers=session_auth_headers(viewer)
    ).json()["data"]["data"]
    return {t["id"]: t for t in rows}


def _unread(client, project_id: str, room_id: str, viewer: str) -> int:
    """The badge map, which omits whatever is at zero — so a missing room IS
    a zero, and the two cases have to read the same here."""
    r = client.get(
        f"/projects/{project_id}/topic-unread", headers=session_auth_headers(viewer)
    )
    assert r.status_code == 200, r.text
    return r.json()["data"].get(room_id, 0)


def test_work_in_a_room_keeps_the_room_alive(client):
    """任务里说话 → 房间的最后活动时间跟着往前走。"""
    p = _project(client)
    room_id = _room(client, p["id"])
    _join(client, room_id, "bob")
    before = _rooms(client, p["id"], "alice")[room_id]["last_activity_at"]

    task_id = _task_of(client, room_id, "bob")
    _say_in_task(client, room_id, task_id, "bob", "我在这条活里干活")

    after = _rooms(client, p["id"], "alice")[room_id]["last_activity_at"]
    assert after > before, "房间里有活在跑，它却看起来一动没动"


def test_work_in_a_room_does_not_light_the_unread_badge(client):
    """任务里说话 → 房间的未读角标不动。

    反过来做的话，一个活都在任务里做的房间会永远顶着红点，而红点里没有一句是给
    房间里的人看的。
    """
    p = _project(client)
    room_id = _room(client, p["id"])
    _join(client, room_id, "bob")
    task_id = _task_of(client, room_id, "bob")

    _say_in_task(client, room_id, task_id, "bob", "我在这条活里干活")
    assert _unread(client, p["id"], room_id, "alice") == 0

    # 房间主线上有人说话才算未读——这一半必须还成立，否则上面那条就是把角标
    # 整个关掉了。
    _say(client, room_id, "bob", "房间里说一句")
    assert _unread(client, p["id"], room_id, "alice") == 1
