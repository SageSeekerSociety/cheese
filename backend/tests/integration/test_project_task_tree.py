"""侧栏那棵树的数据源：项目里的每一个任务，各带它自己的验收卡。

要把「房间 → 它的任务 → 那个任务的 PR」画出来，前端需要一次问全，而不是一个房间
一个请求再一个任务一个请求去拿卡。

所以这里钉的是**一次问全**：两个房间各开一个任务，一个请求把整棵树拿回来，每一行说得出
自己挂在哪个房间。

卡这一栏钉的是它**不许瞎猜**：每个任务递自己的卡，没递卡的那一行就是没有卡，不能把
兄弟任务那张挂上去充数。
"""

from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import open_task, post_project, session_auth_headers


def _project(client) -> str:
    return post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]


def _room(client, project_id: str, title: str) -> str:
    return client.post(
        "/topics",
        json={"project_id": project_id, "title": title},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]


def _task(client, room_id: str, title: str) -> str:
    return open_task(client, room_id, title)["id"]


def _file_card(client, room_id: str, subject: str) -> str:
    r = client.post(
        f"/topics/{delivery_task_id(client, room_id)}/accept-card",
        headers=delivery_headers(client, room_id),
        json={
            "change_subject": subject,
            "reviewer_handle": "alice",
            "focus": "最懂",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _tasks(client, project_id: str) -> list[dict]:
    r = client.get(f"/projects/{project_id}/tasks")
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def test_every_task_in_the_project_comes_back_at_once(client):
    """两个房间各开一个任务，一个请求全拿到——树是一次画出来的。"""
    pid = _project(client)
    room_a = _room(client, pid, "运维")
    room_b = _room(client, pid, "前端")
    _task(client, room_a, "查一下分页接口")
    _task(client, room_b, "改一下侧栏")

    rows = _tasks(client, pid)
    assert {r["title"] for r in rows} == {"查一下分页接口", "改一下侧栏"}
    # 每一行都说得出自己挂在哪个房间——树就是靠这条边建出来的。
    assert {r["room_id"] for r in rows} == {room_a, room_b}


def test_a_task_with_nothing_filed_says_so_rather_than_guessing(client):
    """大多数任务在做的过程中都没有卡。空就是空，不编一个状态出来。"""
    pid = _project(client)
    room = _room(client, pid, "运维")
    _task(client, room, "还在做")

    (row,) = _tasks(client, pid)
    assert row["card"] is None


def test_a_delivery_card_belongs_only_to_its_own_task(client):
    """A sibling's pending card must not appear on unfinished work."""
    pid = _project(client)
    room = _room(client, pid, "运维")
    unfinished = _task(client, room, "一件活")
    card = _file_card(client, room, "chore: independent delivery")
    rows = {row["id"]: row for row in _tasks(client, pid)}
    assert len(rows) == 2
    assert rows[unfinished]["card"] is None
    assert rows[str(delivery_task_id(client, room))]["card"]["id"] == card
