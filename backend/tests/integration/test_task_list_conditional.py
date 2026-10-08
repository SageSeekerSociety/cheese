"""两份任务清单（项目级的、房间级的）的条件请求，和房间清单的 roster 形状。

侧栏每切一次页面就要重读这两份清单，而它们很沉：这个项目 1373 条活 2 MB 出头，一个
房间 1317 条活。两条规矩，都是在人切页面时省下这一趟：

1. 清单没变时服务端只回一行 header，不再把整份 body 传一遍；
2. 画 rail、画概览的调用方一个块都不看，所以 `limit=0` 只带回支线本身 —— 并且带的
   是**每一条**支线，不是「说过话的那几条」。
"""

from tests.integration.conftest import (
    open_task,
    post_message,
    post_project,
    session_auth_headers,
)


def _project(client) -> str:
    return post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]


def _room(client, project_id: str, title: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def test_the_project_task_list_serves_an_etag_and_304(client):
    pid = _project(client)
    room = _room(client, pid, "运维")
    open_task(client, room, "查一下分页接口")

    first = client.get(f"/projects/{pid}/tasks")
    assert first.status_code == 200
    etag = first.headers.get("ETag")
    assert etag, "清单必须带 ETag，否则客户端没有东西可以拿来问"
    # 私有视图：每一行都带「是不是等我」这类按人算的东西，不能被中间代理发给别人。
    assert first.headers.get("Cache-Control") == "private, no-cache"
    assert first.json()["code"] == 200

    again = client.get(f"/projects/{pid}/tasks", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.content == b""
    # RFC 要求 304 也带回 ETag / Cache-Control：客户端要靠它们更新自己的缓存。
    assert again.headers.get("ETag") == etag
    assert again.headers.get("Cache-Control") == "private, no-cache"

    # 真多了一件活：同一个 ETag 不再命中，照常回整份 body。
    open_task(client, room, "改一下侧栏")
    changed = client.get(f"/projects/{pid}/tasks", headers={"If-None-Match": etag})
    assert changed.status_code == 200
    assert changed.headers.get("ETag") != etag
    assert len(changed.json()["data"]["data"]) == 2


def test_the_room_task_list_serves_an_etag_and_304(client):
    pid = _project(client)
    room = _room(client, pid, "运维")
    open_task(client, room, "查一下分页接口")

    first = client.get(f"/topics/{room}/tasks", params={"limit": 0})
    assert first.status_code == 200
    etag = first.headers.get("ETag")
    assert etag
    assert first.headers.get("Cache-Control") == "private, no-cache"

    again = client.get(
        f"/topics/{room}/tasks",
        params={"limit": 0},
        headers={"If-None-Match": etag},
    )
    assert again.status_code == 304
    assert again.content == b""

    # 房间里多了一件活：照常回整份。
    open_task(client, room, "改一下侧栏")
    changed = client.get(
        f"/topics/{room}/tasks",
        params={"limit": 0},
        headers={"If-None-Match": etag},
    )
    assert changed.status_code == 200
    assert changed.headers.get("ETag") != etag
    assert len(changed.json()["data"]["data"]) == 2


def test_limit_zero_returns_every_thread_without_its_conversation(client):
    """rails 只要支线本身。`limit=0` 是「每条最新零个块」，不是「把说过话的筛掉」：
    没人说过话的那条照样要在清单里，否则房间的支线数会随「有没有人开口」变。"""
    pid = _project(client)
    room = _room(client, pid, "运维")
    spoken = open_task(client, room, "说过话的")
    quiet = open_task(client, room, "没人说话的")
    post_message(client, spoken["id"], "alice", {"content": "开始了"})

    r = client.get(f"/topics/{room}/tasks", params={"limit": 0})
    assert r.status_code == 200
    rows = r.json()["data"]["data"]
    assert {row["id"] for row in rows} == {spoken["id"], quiet["id"]}
    for row in rows:
        assert row["blocks"] == [], "调用方不看块，就别让它下载"

    # 不传 limit 仍然是「整份历史」：agent 读历史不能被悄悄截断。
    whole = client.get(f"/topics/{room}/tasks").json()["data"]["data"]
    spoken_row = next(row for row in whole if row["id"] == spoken["id"])
    quiet_row = next(row for row in whole if row["id"] == quiet["id"])
    assert spoken_row["blocks"], "说过话的那条必须带回它的对话"
    assert quiet_row["blocks"] == []
