"""A room is opened with one subscription, not a dozen reads.

The `subscribed` frame carries the room as it stood once the subscription took
hold (`room`): what a page draws around the conversation. Each piece is what its
own route says — a page files them where those routes' answers go — so a room
opened this way and one read piece by piece look the same.

The sidebar does not read the whole project's tasks either: each channel's row
in the topic list carries the caller's tasks under it and how many are underway.
"""

from tests.conftest import wait_work_idle
from tests.integration.conftest import (
    join_project_team,
    open_task,
    post_message,
    post_project,
    room_socket,
    session_auth_headers,
)

ALICE = session_auth_headers("alice")


def _project(client) -> str:
    return post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]


def _room(client, project: str, title: str = "T") -> str:
    r = client.post(
        "/topics", json={"project_id": project, "title": title}, headers=ALICE
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _read(client, path: str, **params):
    r = client.get(path, params=params, headers=ALICE)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _rows(payload) -> list:
    return (
        payload["data"] if isinstance(payload, dict) and "data" in payload else payload
    )


def test_a_channel_opens_with_what_its_routes_say(client):
    room = _room(client, _project(client))
    said = post_message(client, room, "alice", {"content": "定一下发版时间"})
    r = client.put(f"/topics/{room}/pins/{said['id']}", headers=ALICE)
    assert r.status_code == 200, r.text
    open_task(client, room, "还在做", start=False)
    finished = open_task(client, room, "做完了", start=False)
    r = client.post(f"/topics/{finished['id']}/close", json={}, headers=ALICE)
    assert r.status_code == 200, r.text

    with room_socket(client, room, "alice") as ws:
        snapshot = ws.subscribed["room"]

    assert snapshot["members"] == _rows(_read(client, f"/topics/{room}/members"))
    assert snapshot["tasks"]["open"] == _rows(
        _read(client, f"/topics/{room}/tasks", limit=0, status="open")
    )
    assert snapshot["tasks"]["recent"] == _rows(
        _read(client, f"/topics/{room}/tasks", limit=0, status="closed", latest=3)
    )
    assert snapshot["pins"] == _read(client, f"/topics/{room}/pins")
    assert snapshot["threads"] == _read(client, f"/topics/{room}/threads", limit=100)
    assert snapshot["feedback_proposals"] == _read(
        client, f"/topics/{room}/feedback-proposals"
    )
    # What it says is the room: the pin and both tasks are in it.
    assert [pin["block"]["id"] for pin in snapshot["pins"]] == [said["id"]]
    assert [t["title"] for t in snapshot["tasks"]["recent"]] == ["做完了"]


def test_a_task_opens_with_its_own_row_origin_and_review_comments(client):
    room = _room(client, _project(client))
    task = open_task(client, room, "整理周报模板")
    # The task's first turn writes its row as it goes; compare once it is done.
    wait_work_idle()

    with room_socket(client, task["id"], "alice") as ws:
        snapshot = ws.subscribed["room"]

    assert snapshot["task"] == _read(client, f"/topics/{task['id']}/task")
    assert snapshot["related"] == _read(client, f"/topics/{task['id']}/related")
    assert snapshot["review_comments"] == _read(
        client, f"/topics/{task['id']}/review-comments"
    )
    # Whoever may be @-mentioned in the task: its channel's roster.
    assert snapshot["members"] == _rows(_read(client, f"/topics/{room}/members"))
    # A task has no pins or threads of its own to draw.
    assert "pins" not in snapshot and "threads" not in snapshot


def _sidebar(client, project: str, handle: str) -> dict[str, dict]:
    r = client.get(
        "/topics", params={"project_id": project}, headers=session_auth_headers(handle)
    )
    assert r.status_code == 200, r.text
    return {row["id"]: row["my_tasks"] for row in r.json()["data"]["data"]}


def test_a_channel_row_carries_my_tasks_owned_first_and_how_many_are_underway(client):
    project = _project(client)
    join_project_team(client, project, "bob")
    room = _room(client, project)
    other = _room(client, project, "别的频道")
    mine = open_task(client, room, "我的", start=False)
    helping = open_task(
        client, room, "我帮忙的", owner="bob", contributors=["alice"], start=False
    )
    open_task(client, room, "别人的", owner="bob", start=False)

    rail = _sidebar(client, project, "alice")

    assert [t["id"] for t in rail[room]["shown"]] == [mine["id"], helping["id"]]
    assert rail[room]["open"] == 3
    # A channel with nothing underway still has the field, empty.
    assert rail[other] == {"shown": [], "open": 0}
    # Someone else sees their own under the same channel, not mine.
    assert {t["title"] for t in _sidebar(client, project, "bob")[room]["shown"]} == {
        "我帮忙的",
        "别人的",
    }


def test_a_channel_row_shows_at_most_five_of_my_tasks_but_counts_them_all(client):
    project = _project(client)
    room = _room(client, project)
    for n in range(7):
        open_task(client, room, f"第{n}件", start=False)

    rail = _sidebar(client, project, "alice")

    assert len(rail[room]["shown"]) == 5
    assert rail[room]["open"] == 7


def test_a_closed_task_leaves_the_channel_row(client):
    project = _project(client)
    room = _room(client, project)
    done = open_task(client, room, "做完了", start=False)
    r = client.post(f"/topics/{done['id']}/close", json={}, headers=ALICE)
    assert r.status_code == 200, r.text

    assert _sidebar(client, project, "alice")[room] == {"shown": [], "open": 0}


def test_one_topic_carries_the_same_tasks_as_its_row(client):
    project = _project(client)
    room = _room(client, project)
    open_task(client, room, "我的", start=False)

    assert (
        _read(client, f"/topics/{room}")["my_tasks"]
        == _sidebar(client, project, "alice")[room]
    )


def test_what_changes_after_the_snapshot_arrives_as_a_frame_behind_it(client):
    room = _room(client, _project(client))
    said = post_message(client, room, "alice", {"content": "钉住这一条"})

    with room_socket(client, room, "alice") as ws:
        assert ws.subscribed["room"]["pins"] == []
        r = client.put(f"/topics/{room}/pins/{said['id']}", headers=ALICE)
        assert r.status_code == 200, r.text
        for _ in range(20):
            frame = ws.receive_json()
            if frame.get("type") == "state" and frame.get("resource") == "pins":
                break
        else:
            raise AssertionError("the pin never reached the subscription")
