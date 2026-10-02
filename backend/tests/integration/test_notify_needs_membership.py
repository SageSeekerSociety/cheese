"""Writing into a project's inbox takes membership on both ends.

Whoever writes a notification must belong to the project (or the room it points
at): otherwise any signed-in person can put a title and body of their choosing
in the inbox of every member of a project they are not in. Whoever receives it
must be able to open that project or room: a notification its recipient cannot
open reaches nobody, and one about a private room must not show its words to
someone outside that room.
"""

from tests.conftest import seed_user
from tests.integration.conftest import join_project_team, post_project


def _as(client, handle: str) -> dict[str, str]:
    """A signed-in person with a real user row: the bell is read by user id."""
    return {"Authorization": f"Bearer {seed_user(client, handle)}"}


def _project(client, owner: str) -> str:
    r = post_project(client, json={"name": "P"}, owner=owner)
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _room(client, pid: str, by: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": pid, "title": "T"},
        headers=_as(client, by),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _private_chat(client, pid: str, a: str, b: str) -> str:
    r = client.get(
        f"/projects/{pid}/private-chat",
        params={"user_handle": a, "peer_handle": b},
        headers=_as(client, a),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _notify(client, pid: str, by: str, **body):
    return client.post(
        f"/projects/{pid}/alerts",
        json={"level": "light", "kind": "change_alert", "title": "看一下", **body},
        headers=_as(client, by),
    )


def _inbox(client, pid: str, handle: str) -> list[str]:
    """The titles in ``handle``'s own mailbox in this project."""
    r = client.get(f"/projects/{pid}/alerts", headers=_as(client, handle))
    assert r.status_code == 200, r.text
    return [n["title"] for n in r.json()["data"]["data"]]


def test_a_member_notifies_another_member(client):
    pid = _project(client, "alice")
    join_project_team(client, pid, "bob")

    r = _notify(client, pid, "alice", target_handle="bob", title="接口改了")

    assert r.status_code == 200, r.text
    assert "接口改了" in _inbox(client, pid, "bob")


def test_a_stranger_cannot_write_into_someone_elses_project(client):
    pid = _project(client, "alice")
    join_project_team(client, pid, "bob")
    _project(client, "mallory")  # registered, signed in, in a project of her own

    r = _notify(client, pid, "mallory", target_handle="bob", title="点这个链接")

    assert r.status_code == 403, r.text
    assert "点这个链接" not in _inbox(client, pid, "bob")


def test_a_member_cannot_reach_someone_outside_the_project(client):
    pid = _project(client, "alice")
    _project(client, "victor")  # a real user who is not in alice's project

    r = _notify(client, pid, "alice", target_handle="victor", title="你好")

    assert r.status_code == 422, r.text
    assert "你好" not in _inbox(client, pid, "victor")


def test_a_room_of_another_project_is_not_a_place_to_point_at(client):
    pid = _project(client, "alice")
    other = _project(client, "carol")
    elsewhere = _room(client, other, "carol")

    r = _notify(client, pid, "alice", target_handle="alice", topic_id=elsewhere)

    assert r.status_code == 404, r.text


def test_a_private_room_reaches_only_the_people_in_it(client):
    pid = _project(client, "alice")
    for handle in ("bob", "carol"):
        join_project_team(client, pid, handle)
    private = _private_chat(client, pid, "alice", "carol")

    to_outsider = _notify(
        client, pid, "alice", target_handle="bob", topic_id=private, title="私房话"
    )
    from_outsider = _notify(
        client, pid, "bob", target_handle="alice", topic_id=private, title="敲门"
    )

    assert to_outsider.status_code == 422, to_outsider.text
    assert from_outsider.status_code == 403, from_outsider.text
    assert "私房话" not in _inbox(client, pid, "bob")
    assert "敲门" not in _inbox(client, pid, "alice")
