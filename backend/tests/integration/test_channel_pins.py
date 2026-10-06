"""Pins: what a channel's members keep at the top of its overview.

The rules, stated before the code:

- a member pins a message or a file of the channel's main line, in place;
- someone who has not joined the channel cannot pin there, and nothing in an
  archived channel can be pinned;
- pinning says so in the main line, once; pinning again says nothing more, and
  unpinning says nothing;
- anyone who reads the channel sees its pins;
- what is said in a 支线 is not pinned to the channel.
"""

import uuid

from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)


def _setup(client):
    """dave's project with a channel alice made; bob is in the project and has
    not joined it."""
    p = post_project(client, json={"name": "P"}, owner="dave").json()["data"]
    for handle in ("alice", "bob"):
        join_project_team(client, p["id"], handle)
    r = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "前端"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _say(client, tid, who, text):
    r = client.post(
        f"/topics/{tid}/messages",
        json={"request_id": str(uuid.uuid4()), "content": text},
        headers=session_auth_headers(who),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _pin(client, tid, block, who):
    return client.put(f"/topics/{tid}/pins/{block}", headers=session_auth_headers(who))


def _pins(client, tid, who="bob"):
    r = client.get(f"/topics/{tid}/pins", headers=session_auth_headers(who))
    assert r.status_code == 200, r.text
    return [row["block"]["id"] for row in r.json()["data"]]


def _pin_lines(client, tid):
    blocks = client.get(
        f"/topics/{tid}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    return [b for b in blocks if (b.get("meta") or {}).get("pinned_block_id")]


def test_a_member_pins_and_everyone_sees_it(client):
    tid = _setup(client)
    message = _say(client, tid, "alice", "周四 18:00 之后只合修复")

    assert _pin(client, tid, message, "alice").status_code == 200

    assert _pins(client, tid, who="bob") == [message]


def test_pinning_is_said_once_and_unpinning_is_quiet(client):
    tid = _setup(client)
    message = _say(client, tid, "alice", "周四 18:00 之后只合修复")

    _pin(client, tid, message, "alice")
    _pin(client, tid, message, "alice")
    lines = _pin_lines(client, tid)
    assert [line["meta"]["pinned_block_id"] for line in lines] == [message]

    r = client.delete(
        f"/topics/{tid}/pins/{message}", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text
    assert _pins(client, tid) == []
    assert len(_pin_lines(client, tid)) == 1


def test_someone_who_has_not_joined_cannot_pin(client):
    tid = _setup(client)
    message = _say(client, tid, "alice", "周四 18:00 之后只合修复")

    assert _pin(client, tid, message, "bob").status_code == 403
    assert _pins(client, tid) == []


def test_nothing_is_pinned_in_an_archived_channel(client):
    tid = _setup(client)
    message = _say(client, tid, "alice", "周四 18:00 之后只合修复")
    r = client.post(f"/topics/{tid}/archive", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text

    assert _pin(client, tid, message, "alice").status_code == 403
    assert _pins(client, tid) == []


def test_a_reply_in_a_thread_is_not_pinned_to_the_channel(client):
    tid = _setup(client)
    message = _say(client, tid, "alice", "预览在微信里打不开")
    r = client.post(f"/blocks/{message}/thread", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    reply = _say(client, r.json()["data"]["id"], "alice", "我看看")

    assert _pin(client, tid, reply, "alice").status_code == 404
    assert _pins(client, tid) == []
