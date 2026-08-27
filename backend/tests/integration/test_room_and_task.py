"""A room outlives the work done in it.

A 话题 used to be a room, a task, a workspace and a session at once, so the
shortest lifetime won: accepting the work took the room with it. Work is now a
THREAD in a room — a `tasks` row, not a `topics` row — which is what lets the
room stay open for the next piece of work, and for anything delivered into it
later.
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
    """One piece of work, dispatched into a room.

    Through `/split`, because that is the only way to make one: `POST /topics`
    under a room is refused now — a room's inside is work, not another room.
    """
    r = client.post(f"/topics/{room_id}/split", json={"title": title})
    assert r.status_code == 200, r.text
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


def test_work_inside_a_room_is_a_thread_not_a_nested_room(client):
    project_id = _project(client)
    room_id = _room(client, project_id)
    assert client.get(f"/topics/{room_id}").json()["data"]["kind"] == "topic"

    task = _task(client, project_id, room_id, "修登录")
    assert task["room_id"] == room_id
    # And a room under a room is refused outright rather than quietly made.
    refused = client.post(
        "/topics",
        json={"project_id": project_id, "title": "第二个房间", "parent_id": room_id},
    )
    assert refused.status_code == 422


def test_accepting_the_batch_leaves_the_room_and_its_work_open(client):
    """The whole point: delivering ends the delivery, not the place.

    Before tasks existed, this accept archived the only object there was, and
    the room, its roster and its history went with it. Since #442 decision 1 it
    does not archive anything at all — 「这件事做完了」lives on the card, and
    putting a row away is a person's decision.

    递卡是房间的事（一棵树=一个分支=一个 PR=一批活），所以采纳的是**一批**活，
    不是其中某一件。这也是为什么下面不去读那件活的 `accepted_at`：那个标记只在
    卡指名了某条支线时才盖，而现在没有卡会指名任何一条。**那个标记因此永远是空的，
    而房间反倒被盖上了「已交付」**——见 `AcceptService._stamp_delivery` 的注释，
    那正是它当初要避免的读法。这属于 one-tree-per-PR 之后「一个 PR 怎么记一整棵树
    的交付」那个待决问题，不是这里能回答的。
    """
    project_id = _project(client)
    room_id = _room(client, project_id)
    task = _task(client, project_id, room_id, "修登录")

    _accept(client, room_id)

    assert _status(client, task["id"]) == "open"
    assert _status(client, room_id) == "active"


def test_a_room_takes_a_second_task_after_the_first_is_accepted(client):
    """A durable room accumulates work over time, so the second piece of work
    needs somewhere to live that isn't the first one's branch."""
    project_id = _project(client)
    room_id = _room(client, project_id)

    first = _task(client, project_id, room_id, "修登录")
    _accept(client, room_id)
    second = _task(client, project_id, room_id, "加导出")
    assert _status(client, first["id"]) == "open"

    assert second["room_id"] == room_id
    assert _status(client, second["id"]) == "open"
    assert _status(client, room_id) == "active"


def test_archiving_the_room_still_takes_its_tasks(client):
    """Cascade downward is unchanged: closing the place closes the work in it —
    a piece of work with no room has no context and no way back.

    The two ends use different words on purpose: a room is `archived` (a person
    put it away and its work面 froze); a thread is `closed` (its work ended).
    """
    project_id = _project(client)
    room_id = _room(client, project_id)
    task = _task(client, project_id, room_id, "修登录")

    r = client.post(f"/topics/{room_id}/archive", json={"by": "alice"})
    assert r.status_code == 200

    assert _status(client, room_id) == "archived"
    assert _status(client, task["id"]) == "closed"
