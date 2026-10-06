"""支线: the replies under a message in a channel.

A person calling 芝士 in a channel's main line is answered in that message's
支线, never in the main line. Anyone can reply under any message, and the
支线 they open is the message's one 支线. Only the people who took part are
told of new replies, and only of what people said there.
"""

import time

from tests.integration.conftest import (
    join_project_team,
    post_message,
    post_project,
    session_auth_headers,
)


def _channel(client, owner: str = "alice") -> tuple[str, str]:
    project = post_project(client, json={"name": "支线"}, owner=owner).json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "前端"},
        headers=session_auth_headers(owner),
    ).json()["data"]
    return project["id"], room["id"]


def _blocks(client, conversation: str, handle: str = "alice") -> list[dict]:
    r = client.get(
        f"/topics/{conversation}/blocks", headers=session_auth_headers(handle)
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _open(client, block_id: str, handle: str = "alice") -> dict:
    r = client.post(f"/blocks/{block_id}/thread", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _answer(blocks: list[dict], question: dict) -> list[dict]:
    """What 芝士 did and said answering ``question``: the blocks of its turn."""
    return [
        b
        for b in blocks
        if b["turn_id"] == question["id"] and b["id"] != question["id"]
    ]


def _wait_for(predicate, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = predicate()
        if found:
            return found
        time.sleep(0.2)
    raise AssertionError("timed out")


def test_calling_cheese_in_the_main_line_is_answered_in_that_messages_thread(
    client,
):
    _project, room = _channel(client)
    asked = post_message(client, room, "alice", {"content": "@芝士 首页要改哪些地方？"})

    # The question has a 支线, and the answer is in it.
    thread = _open(client, asked["id"])
    _wait_for(lambda: _answer(_blocks(client, thread["id"]), asked))

    # The main line carries the question, not the answer.
    main = _blocks(client, room)
    assert [b["id"] for b in main] == [asked["id"]]


def test_the_main_line_shows_how_a_thread_grew(client):
    _project, room = _channel(client)
    said = post_message(client, room, "alice", {"content": "下午三点评审"})
    thread = _open(client, said["id"])
    post_message(client, thread["id"], "alice", {"content": "改到四点"})

    under = next(b for b in _blocks(client, room) if b["id"] == said["id"])
    assert under["thread"]["id"] == thread["id"]
    assert under["thread"]["reply_count"] == 1
    assert under["thread"]["last_reply"]["content"] == "改到四点"


def test_a_message_has_one_thread_however_often_it_is_opened(client):
    project, room = _channel(client)
    join_project_team(client, project, "bob")
    said = post_message(client, room, "alice", {"content": "下午三点评审"})

    first = _open(client, said["id"], "alice")
    again = _open(client, said["id"], "bob")
    assert again["id"] == first["id"]


def test_a_thread_only_hangs_under_a_channel_message(client):
    _project, room = _channel(client)
    said = post_message(client, room, "alice", {"content": "下午三点评审"})
    thread = _open(client, said["id"])
    reply = post_message(client, thread["id"], "alice", {"content": "收到"})

    r = client.post(
        f"/blocks/{reply['id']}/thread", headers=session_auth_headers("alice")
    )
    assert r.status_code == 422, r.text


def test_new_replies_are_news_only_to_the_people_who_took_part(client):
    project, room = _channel(client)
    join_project_team(client, project, "bob")
    join_project_team(client, project, "carol")
    joined = client.post(f"/topics/{room}/join", headers=session_auth_headers("bob"))
    assert joined.status_code == 200, joined.text
    said = post_message(client, room, "bob", {"content": "登录页要不要加记住我？"})
    thread = _open(client, said["id"], "alice")
    post_message(client, thread["id"], "alice", {"content": "要，七天"})

    def unread_for(handle: str) -> bool:
        r = client.get(f"/topics/{room}/threads", headers=session_auth_headers(handle))
        assert r.status_code == 200, r.text
        (row,) = r.json()["data"]
        assert row["reply_count"] == 1
        return row["unread"]

    # bob wrote the message the 支线 hangs under: alice's reply is news to him.
    assert unread_for("bob") is True
    # alice said it herself, and carol never took part.
    assert unread_for("alice") is False
    assert unread_for("carol") is False

    r = client.post(
        f"/topics/{thread['id']}/read", json={}, headers=session_auth_headers("bob")
    )
    assert r.status_code == 200, r.text
    assert unread_for("bob") is False
