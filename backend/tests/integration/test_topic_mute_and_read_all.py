"""话题静音和「全部标为已读」。

- 静音按人记：我静音一间房，别人那边照旧；静音不等于已读，房间自己的未读数还在，
  只是侧栏不把它算进总数（那一半在前端）。
- 「全部标为已读」只动我在这个项目里有未读的房间，读完之后角标表是空的；别人的不动。
"""

from tests.integration.conftest import session_auth_headers
from tests.integration.test_room_activity_vs_unread import (
    _join,
    _project,
    _room,
    _say,
    _unread,
)


def _levels(client, project_id: str, viewer: str) -> dict[str, str]:
    r = client.get(
        f"/projects/{project_id}/topic-notify-levels",
        headers=session_auth_headers(viewer),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _set_level(client, room_id: str, viewer: str, level: str):
    return client.put(
        f"/topics/{room_id}/notify-level",
        json={"level": level},
        headers=session_auth_headers(viewer),
    )


def test_mute_is_per_person_and_is_not_reading(client):
    p = _project(client)
    room_id = _room(client, p["id"])
    _join(client, room_id, "bob")
    _say(client, room_id, "bob", "一句")

    r = _set_level(client, room_id, "alice", "mute")
    assert r.status_code == 200, r.text
    assert _levels(client, p["id"], "alice") == {room_id: "mute"}
    assert _levels(client, p["id"], "bob") == {}
    # 静音不是已读：那一句还在 alice 的未读里。
    assert _unread(client, p["id"], room_id, "alice") == 1

    assert _set_level(client, room_id, "alice", "all").status_code == 200
    assert _levels(client, p["id"], "alice") == {}


def test_an_unknown_level_is_refused(client):
    p = _project(client)
    room_id = _room(client, p["id"])
    r = _set_level(client, room_id, "alice", "loud")
    assert r.status_code == 422, r.text


def test_read_all_clears_my_badges_and_only_mine(client):
    p = _project(client)
    first = _room(client, p["id"])
    second = _room(client, p["id"])
    for room_id in (first, second):
        _join(client, room_id, "bob")
        _say(client, room_id, "bob", "一句")
    _say(client, first, "alice", "alice 也说一句")

    r = client.post(
        f"/projects/{p['id']}/read-all", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text
    assert sorted(r.json()["data"]["topic_ids"]) == sorted([first, second])
    assert _unread(client, p["id"], first, "alice") == 0
    assert _unread(client, p["id"], second, "alice") == 0
    # bob 的未读（alice 那一句）不受影响。
    assert _unread(client, p["id"], first, "bob") == 1
