"""A page reads the tasks it draws, not the room's whole task list.

The conversation page carries each task with the block it belongs to: the tasks
made from a message under that message, and the task a room-line row says began
there on that row. The room's task list narrows to what a panel shows: open or
closed, the latest few, these tasks, the ones these blocks carry, the ones with
a branch.
"""

from tests.integration.conftest import (
    open_task,
    post_message,
    post_project,
    session_auth_headers,
)

ALICE = session_auth_headers("alice")


def _room(client) -> str:
    pid = post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    return client.post(
        "/topics", json={"project_id": pid, "title": "T"}, headers=ALICE
    ).json()["data"]["id"]


def _page(client, room: str) -> list[dict]:
    r = client.get(f"/topics/{room}/blocks?limit=50&shown=true", headers=ALICE)
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _tasks(client, room: str, **params) -> list[dict]:
    r = client.get(f"/topics/{room}/tasks", params=params, headers=ALICE)
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _from_message(client, room: str, content: str) -> tuple[dict, dict]:
    said = post_message(client, room, "alice", {"content": content})
    r = client.post(f"/blocks/{said['id']}/upgrade", headers=ALICE)
    assert r.status_code == 200, r.text
    return said, r.json()["data"]


def test_a_task_made_from_a_message_comes_under_that_message(client):
    room = _room(client)
    said, task = _from_message(client, room, "把导出改成后台任务")

    [message] = [b for b in _page(client, room) if b["id"] == said["id"]]

    assert [t["id"] for t in message["tasks"]] == [task["id"]]
    # The row the room's task list has, cell and all.
    assert message["tasks"][0]["presentation"]["column"]


def test_a_task_started_in_the_room_comes_on_the_row_that_says_so(client):
    room = _room(client)
    task = open_task(client, room, "整理周报模板", start=False)

    rows = [b for b in _page(client, room) if b.get("tasks")]

    assert [(b["meta"]["action"], [t["id"] for t in b["tasks"]]) for b in rows] == [
        ("task_created", [task["id"]])
    ]


def test_a_panel_reads_only_the_tasks_it_shows(client):
    room = _room(client)
    kept = open_task(client, room, "还在做", start=False)
    done = []
    for title in ("第一件", "第二件", "第三件", "第四件"):
        task = open_task(client, room, title, start=False)
        r = client.post(
            f"/topics/{task['id']}/close", json={"conclusion": "好了"}, headers=ALICE
        )
        assert r.status_code == 200, r.text
        done.append(task["id"])

    assert [t["id"] for t in _tasks(client, room, status="open", limit=0)] == [
        kept["id"]
    ]
    latest = _tasks(client, room, status="closed", latest=3, limit=0)
    assert [t["id"] for t in latest] == done[::-1][:3]
    assert [t["id"] for t in _tasks(client, room, ids=[done[0]], limit=0)] == [done[0]]


def test_the_tasks_some_blocks_carry_are_read_by_those_blocks(client):
    room = _room(client)
    said, from_message = _from_message(client, room, "这条拆出去")
    started = open_task(client, room, "直接开的", start=False)
    open_task(client, room, "别的", start=False)
    line = next(
        b
        for b in _page(client, room)
        if (b.get("meta") or {}).get("task_id") == started["id"]
    )

    carried = _tasks(client, room, blocks=[said["id"], line["id"]], limit=0)

    assert {t["id"] for t in carried} == {from_message["id"], started["id"]}
