"""Private channels: only the people in one see it.

A private channel, and every task in it, is out of sight for everyone else in
the project — in the sidebar and the channel list, in search, in the task lists,
in notifications, and to an AI teammate not seated in it — and a direct read of
it answers as if it did not exist. Its people bring others in; nobody joins. Its
managers make a public channel private; only someone who manages the project
makes a private one public again. 综合 is never private.
"""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import (
    join_project_team,
    post_message,
    post_project,
    session_auth_headers,
)


def _project(client) -> dict:
    """dave's project; alice, bob and carol are on its team."""
    p = post_project(client, json={"name": "P"}, owner="dave").json()["data"]
    for handle in ("alice", "bob", "carol"):
        join_project_team(client, p["id"], handle)
    return p


def _channel(client, pid: str, *, by: str = "alice", private: bool = True) -> str:
    r = client.post(
        "/topics",
        json={"project_id": pid, "title": "机密频道", "members_only": private},
        headers=session_auth_headers(by),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["members_only"] is private
    return r.json()["data"]["id"]


def _listed(client, pid: str, who: str) -> set[str]:
    r = client.get(
        "/topics", params={"project_id": pid}, headers=session_auth_headers(who)
    )
    assert r.status_code == 200, r.text
    return {t["id"] for t in r.json()["data"]["data"]}


def _add(client, tid: str, handle: str, *, by: str):
    return client.post(
        f"/topics/{tid}/members",
        json={"handle": handle},
        headers=session_auth_headers(by),
    )


def _make(client, tid: str, private: bool, *, by: str):
    return client.put(
        f"/topics/{tid}/members-only",
        json={"members_only": private},
        headers=session_auth_headers(by),
    )


def _task(client, tid: str, by: str = "alice") -> str:
    r = client.post(
        f"/topics/{tid}/tasks",
        json={"title": "季度预算核对"},
        headers=session_auth_headers(by),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _alerts(client, pid: str, who: str) -> list[dict]:
    r = client.get(f"/projects/{pid}/alerts", headers=session_auth_headers(who))
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _seated_agent(client, tid: str) -> str:
    rows = client.get(
        f"/topics/{tid}/members", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    return next(row["member_handle"] for row in rows if row["agent"])


# ---- who sees it -------------------------------------------------------------


def test_only_its_people_find_a_private_channel_in_the_lists(client):
    p = _project(client)
    tid = _channel(client, p["id"])

    assert tid in _listed(client, p["id"], "alice")
    assert tid not in _listed(client, p["id"], "bob")
    # Not even whoever manages the project, from outside it.
    assert tid not in _listed(client, p["id"], "dave")

    names = client.get("/topics/names", headers=session_auth_headers("bob"))
    assert tid not in {t["id"] for t in names.json()["data"]["topics"]}
    children = client.get(
        f"/topics/{p['root_topic_id']}/children", headers=session_auth_headers("bob")
    )
    assert children.status_code == 200, children.text
    assert tid not in {t["id"] for t in children.json()["data"]["data"]}


def test_someone_outside_reads_it_as_a_channel_that_does_not_exist(client):
    p = _project(client)
    tid = _channel(client, p["id"])
    post_message(client, tid, "alice", {"content": "只给在场的人看"})
    nowhere = str(uuid.uuid4())

    for path in ("", "/blocks", "/members"):
        seen = client.get(f"/topics/{tid}{path}", headers=session_auth_headers("bob"))
        missing = client.get(
            f"/topics/{nowhere}{path}", headers=session_auth_headers("bob")
        )
        assert seen.status_code == 404, (path, seen.text)
        assert seen.json()["message"] == missing.json()["message"]
    assert (
        client.get(f"/topics/{tid}/blocks", headers=session_auth_headers("alice"))
    ).status_code == 200
    joined = client.post(f"/topics/{tid}/join", headers=session_auth_headers("bob"))
    assert joined.status_code == 404, joined.text


def test_a_task_in_a_private_channel_is_as_private_as_the_channel(client):
    p = _project(client)
    tid = _channel(client, p["id"])
    task = _task(client, tid)

    def tasks(who):
        r = client.get(f"/projects/{p['id']}/tasks", headers=session_auth_headers(who))
        assert r.status_code == 200, r.text
        return {t["id"] for t in r.json()["data"]["data"]}

    assert task in tasks("alice")
    assert task not in tasks("bob")
    assert (
        client.get(f"/topics/{task}/task", headers=session_auth_headers("bob"))
    ).status_code == 404
    assert (
        client.get(f"/topics/{task}/task", headers=session_auth_headers("alice"))
    ).status_code == 200


def test_search_finds_a_private_channel_only_for_its_people(client):
    p = _project(client)
    tid = _channel(client, p["id"])
    post_message(client, tid, "alice", {"content": "紫色长颈鹿的预算"})
    _task(client, tid)

    def hits(who):
        r = client.get(
            f"/projects/{p['id']}/context/search",
            params={"q": "预算"},
            headers=session_auth_headers(who),
        )
        assert r.status_code == 200, r.text
        return r.json()["data"]["hits"]

    mine = hits("alice")
    assert any(h["room_id"] == tid for h in mine["records"])
    assert [t["title"] for t in mine["tasks"]] == ["季度预算核对"]
    theirs = hits("bob")
    assert not any(h["room_id"] == tid for h in theirs["records"])
    assert theirs["tasks"] == []


def test_an_ai_teammate_not_seated_in_it_does_not_see_it(client):
    p = _project(client)
    root = p["root_topic_id"]
    tid = _channel(client, p["id"])
    task = _task(client, tid)
    made = client.post(
        f"/projects/{p['id']}/agents",
        json={"handle": "planner", "display_name": "规划师"},
        headers=session_auth_headers("dave"),
    )
    assert made.status_code == 200, made.text
    planner = made.json()["data"]["seat_handle"]
    assert _add(client, root, planner, by="dave").status_code == 200
    auth = {
        "X-Cheese-Token": mint_scoped_token(
            project_id=p["id"],
            topic_id=root,
            access_scope="project",
            agent_handle=planner,
        )
    }

    assert client.get(f"/topics/{tid}/blocks", headers=auth).status_code == 404
    rooms = client.get(
        "/topics", params={"project_id": p["id"], "topic": root}, headers=auth
    )
    assert rooms.status_code == 200, rooms.text
    assert tid not in {t["id"] for t in rooms.json()["data"]["data"]}
    tasks = client.get(
        f"/projects/{p['id']}/tasks", params={"topic": root}, headers=auth
    )
    assert tasks.status_code == 200, tasks.text
    assert task not in {t["id"] for t in tasks.json()["data"]["data"]}

    # The channel's own AI teammate is in it, and reads it.
    seated = {
        "X-Cheese-Token": mint_scoped_token(
            project_id=p["id"],
            topic_id=tid,
            access_scope="project",
            agent_handle=_seated_agent(client, tid),
        )
    }
    assert client.get(f"/topics/{tid}/blocks", headers=seated).status_code == 200


# ---- what reaches people outside it -----------------------------------------


def test_a_mention_of_someone_outside_does_not_reach_them(client):
    p = _project(client)
    tid = _channel(client, p["id"])
    assert _add(client, tid, "bob", by="alice").status_code == 200

    post_message(client, tid, "alice", {"content": "<@bob> <@carol> 看一下"})

    assert [a["kind"] for a in _alerts(client, p["id"], "bob")].count("MENTION") == 1
    assert not any(a["kind"] == "MENTION" for a in _alerts(client, p["id"], "carol"))


def test_its_name_is_not_linked_from_another_channel(client):
    p = _project(client)
    secret = _channel(client, p["id"])
    shown = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "公开频道"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    said = post_message(
        client,
        p["root_topic_id"],
        "alice",
        {"content": "去 @公开频道 和 @机密频道 看"},
    )
    assert f"<#{shown}>" in said["content"]
    assert secret not in said["content"]


def test_archiving_it_is_said_inside_it_not_in_general(client):
    p = _project(client)
    tid = _channel(client, p["id"])
    archived = client.post(
        f"/topics/{tid}/archive", headers=session_auth_headers("alice")
    )
    assert archived.status_code == 200, archived.text

    general = client.get(
        f"/topics/{p['root_topic_id']}/blocks", headers=session_auth_headers("bob")
    ).json()["data"]["data"]
    assert not any("机密频道" in (b.get("content") or "") for b in general)
    inside = client.get(
        f"/topics/{tid}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    assert any("机密频道" in (b.get("content") or "") for b in inside)


# ---- who is in it ------------------------------------------------------------


def test_its_people_bring_others_in(client):
    p = _project(client)
    tid = _channel(client, p["id"])

    # Someone outside cannot add anyone, themselves included.
    assert _add(client, tid, "carol", by="bob").status_code == 404
    assert _add(client, tid, "bob", by="bob").status_code == 404

    assert _add(client, tid, "bob", by="alice").status_code == 200
    assert tid in _listed(client, p["id"], "bob")
    # bob does not manage the channel, and still brings carol in.
    assert _add(client, tid, "carol", by="bob").status_code == 200
    assert tid in _listed(client, p["id"], "carol")

    # Taking someone out is still its managers', from inside it.
    def remove(who):
        return client.delete(
            f"/topics/{tid}/members/carol", headers=session_auth_headers(who)
        )

    assert remove("bob").status_code == 403
    assert remove("dave").status_code == 404

    left = client.post(f"/topics/{tid}/leave", headers=session_auth_headers("carol"))
    assert left.status_code == 200, left.text
    assert tid not in _listed(client, p["id"], "carol")
    assert (
        client.get(f"/topics/{tid}/blocks", headers=session_auth_headers("carol"))
    ).status_code == 404


# ---- making it private, and public again ------------------------------------


def test_its_manager_makes_a_public_channel_private(client):
    p = _project(client)
    tid = _channel(client, p["id"], private=False)
    assert tid in _listed(client, p["id"], "bob")

    assert _make(client, tid, True, by="bob").status_code == 403
    made = _make(client, tid, True, by="alice")
    assert made.status_code == 200, made.text
    assert made.json()["data"]["members_only"] is True

    assert tid not in _listed(client, p["id"], "bob")
    assert tid in _listed(client, p["id"], "alice")


def test_whoever_manages_the_project_makes_it_private_and_stays_in_it(client):
    p = _project(client)
    tid = _channel(client, p["id"], private=False)
    assert _make(client, tid, True, by="dave").status_code == 200
    assert tid in _listed(client, p["id"], "dave")


def test_only_whoever_manages_the_project_makes_it_public_again(client):
    p = _project(client)
    tid = _channel(client, p["id"])

    # Its creator manages it, and still cannot show its history to everyone.
    assert _make(client, tid, False, by="alice").status_code == 403
    # Whoever manages the project does so from inside it.
    assert _make(client, tid, False, by="dave").status_code == 404
    assert _add(client, tid, "dave", by="alice").status_code == 200
    made = _make(client, tid, False, by="dave")
    assert made.status_code == 200, made.text

    assert tid in _listed(client, p["id"], "carol")
    assert (
        client.get(f"/topics/{tid}/blocks", headers=session_auth_headers("carol"))
    ).status_code == 200


def test_general_is_never_private(client):
    p = _project(client)
    r = _make(client, p["root_topic_id"], True, by="dave")
    assert r.status_code == 422, r.text
    assert p["root_topic_id"] in _listed(client, p["id"], "bob")
