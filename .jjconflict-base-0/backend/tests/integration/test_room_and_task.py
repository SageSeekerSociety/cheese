"""A room outlives the work done in it.

A 话题 used to be a room, a task, a workspace and a session at once, so the
shortest lifetime won: accepting the work took the room with it. Work is now its
own kind (``task``) inside a room, which is what lets the room stay open for the
next piece of work — and for anything delivered into it later.
"""

from tests.integration.conftest import session_auth_headers


def _project(client) -> str:
    return client.post("/projects", json={"name": "P"}).json()["data"]["id"]


def _room(client, project_id: str, title: str = "运维") -> str:
    """A child of the project root — a place you talk in."""
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title, "created_by": "alice"},
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _task(client, project_id: str, room_id: str, title: str) -> dict:
    """A child of a room — one piece of work."""
    r = client.post(
        "/topics",
        json={
            "project_id": project_id,
            "title": title,
            "parent_id": room_id,
            "created_by": "alice",
        },
    )
    assert r.status_code == 200
    return r.json()["data"]


def _accept(client, topic_id: str) -> None:
    card = client.post(
        f"/topics/{topic_id}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
        },
    ).json()["data"]
    r = client.post(
        f"/accept-cards/{card['id']}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200


def _status(client, topic_id: str) -> str:
    return client.get(f"/topics/{topic_id}").json()["data"]["status"]


def test_work_inside_a_room_is_a_task_not_a_nested_room(client):
    project_id = _project(client)
    room_id = _room(client, project_id)
    assert client.get(f"/topics/{room_id}").json()["data"]["kind"] == "topic"
    assert _task(client, project_id, room_id, "修登录")["kind"] == "task"


def test_accepting_a_task_leaves_its_room_open(client):
    """The whole point: finishing a piece of work ends that work, not the place.

    Before tasks existed, this accept archived the only object there was, and
    the room, its roster and its history went with it.
    """
    project_id = _project(client)
    room_id = _room(client, project_id)
    task = _task(client, project_id, room_id, "修登录")

    _accept(client, task["id"])

    assert _status(client, task["id"]) == "archived"
    assert _status(client, room_id) == "active"


def test_a_room_takes_a_second_task_after_the_first_is_accepted(client):
    """A durable room accumulates work over time, so the second piece of work
    needs somewhere to live that isn't the first one's branch."""
    project_id = _project(client)
    room_id = _room(client, project_id)

    first = _task(client, project_id, room_id, "修登录")
    _accept(client, first["id"])
    second = _task(client, project_id, room_id, "加导出")

    assert second["kind"] == "task"
    assert second["parent_id"] == room_id
    assert _status(client, second["id"]) == "active"
    assert _status(client, room_id) == "active"


def test_archiving_the_room_still_takes_its_tasks(client):
    """Cascade downward is unchanged: closing the place closes the work in it —
    an orphaned task with no room has no context and no way back."""
    project_id = _project(client)
    room_id = _room(client, project_id)
    task = _task(client, project_id, room_id, "修登录")

    r = client.post(f"/topics/{room_id}/archive", json={"by": "alice"})
    assert r.status_code == 200

    assert _status(client, room_id) == "archived"
    assert _status(client, task["id"]) == "archived"
