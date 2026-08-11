"""A room outlives the work done in it.

A 话题 used to be a room, a task, a workspace and a session at once, so the
shortest lifetime won: accepting the work took the room with it. Work is now its
own kind (``task``) inside a room, which is what lets the room stay open for the
next piece of work — and for anything delivered into it later.
"""

from tests.integration.conftest import session_auth_headers


def _project(client) -> str:
    return client.post("/api/projects", json={"name": "P"}).json()["data"]["id"]


def _room(client, project_id: str, title: str = "运维") -> str:
    """A child of the project root — a place you talk in."""
    r = client.post(
        "/api/topics",
        json={"project_id": project_id, "title": title, "created_by": "alice"},
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _task(client, project_id: str, room_id: str, title: str) -> dict:
    """A child of a room — one piece of work."""
    r = client.post(
        "/api/topics",
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
        f"/api/topics/{topic_id}/accept-card",
        json={"reviewer_handle": "alice", "routing_reason": "最懂"},
    ).json()["data"]
    r = client.post(
        f"/api/accept-cards/{card['id']}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200


def _status(client, topic_id: str) -> str:
    return client.get(f"/api/topics/{topic_id}").json()["data"]["status"]


def test_work_inside_a_room_is_a_task_not_a_nested_room(client):
    project_id = _project(client)
    room_id = _room(client, project_id)
    assert client.get(f"/api/topics/{room_id}").json()["data"]["kind"] == "topic"
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

    r = client.post(f"/api/topics/{room_id}/archive", json={"by": "alice"})
    assert r.status_code == 200

    assert _status(client, room_id) == "archived"
    assert _status(client, task["id"]) == "archived"


def test_work_does_not_nest_a_task_under_a_task(client):
    """A task is the leaf. Asking for a child of one gives a task in the same
    room, not a second level of work — the tree stays 本体 > 房间 > 事.

    It used to just make a task under a task, so 分身 could sit under 分身 and
    the room stopped listing the work that belonged to it.
    """
    project_id = _project(client)
    room_id = _room(client, project_id)
    task = _task(client, project_id, room_id, "修登录")

    child = _task(client, project_id, task["id"], "顺手加个测试")

    assert child["kind"] == "task"
    assert child["parent_id"] == room_id
    assert child["id"] != task["id"]


def test_splitting_from_inside_a_task_lands_the_new_task_beside_it(client):
    """分身 finds its job is really two jobs: 拆 still works, the result is a
    sibling in the room rather than a child of the splitter."""
    project_id = _project(client)
    room_id = _room(client, project_id)
    task = _task(client, project_id, room_id, "修登录")

    r = client.post(
        f"/api/topics/{task['id']}/split",
        json={"title": "顺手加个测试", "created_by": "alice", "brief": "补测试"},
    )
    assert r.status_code == 200
    split = r.json()["data"]

    assert split["kind"] == "task"
    assert split["parent_id"] == room_id
    # The room lists both pieces of work; neither hides under the other.
    children = client.get(f"/api/topics/{room_id}/children").json()["data"]["data"]
    assert {c["id"] for c in children} >= {task["id"], split["id"]}


def test_accepting_a_room_takes_the_work_still_open_inside_it(client):
    """采纳即归档 cascades, exactly like manual 归档 already did.

    Accepting a room used to archive the room alone and leave its tasks running
    — 分身 working on a delivered parent, each holding a sandbox container for
    work nobody could hand in anymore.
    """
    project_id = _project(client)
    room_id = _room(client, project_id)
    unfinished = _task(client, project_id, room_id, "还没干完的活")

    _accept(client, room_id)

    assert _status(client, room_id) == "archived"
    assert _status(client, unfinished["id"]) == "archived"


def test_accepting_a_task_still_leaves_its_siblings_alone(client):
    """The cascade only goes down. A room's OTHER work is not collateral."""
    project_id = _project(client)
    room_id = _room(client, project_id)
    first = _task(client, project_id, room_id, "修登录")
    second = _task(client, project_id, room_id, "加导出")

    _accept(client, first["id"])

    assert _status(client, first["id"]) == "archived"
    assert _status(client, second["id"]) == "active"
    assert _status(client, room_id) == "active"
