"""Chapters: the outline of a conversation.

The rules, stated before the code:

- a message in a channel's main line, a task or a 支线 can be marked as the
  start of a chapter;
- everyone who reads the conversation reads its chapters, in reading order;
- in a channel's main line only its members mark one; a task is spoken in by
  everyone in the project, so anyone there may organize it;
- marking twice leaves one chapter and says nothing, and unmarking says
  nothing;
- a message from another conversation cannot be marked here;
- nothing is marked in an archived channel.
"""

import uuid

from tests.integration.conftest import (
    join_project_team,
    open_task,
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


def _mark(client, tid, block, who):
    return client.put(
        f"/topics/{tid}/chapters/{block}", headers=session_auth_headers(who)
    )


def _chapters(client, tid, who="bob"):
    r = client.get(f"/topics/{tid}/chapters", headers=session_auth_headers(who))
    assert r.status_code == 200, r.text
    return [row["block"]["id"] for row in r.json()["data"]]


def test_a_member_marks_a_chapter_and_everyone_reads_it(client):
    tid = _setup(client)
    message = _say(client, tid, "alice", "先看登录")

    assert _mark(client, tid, message, "alice").status_code == 200

    assert _chapters(client, tid, who="bob") == [message]


def test_chapters_read_in_the_conversation_s_order(client):
    tid = _setup(client)
    first = _say(client, tid, "alice", "先看登录")
    second = _say(client, tid, "alice", "再看发布")

    _mark(client, tid, second, "alice")
    _mark(client, tid, first, "alice")

    assert _chapters(client, tid) == [first, second]


def test_marking_twice_leaves_one_and_unmarking_is_quiet(client):
    tid = _setup(client)
    message = _say(client, tid, "alice", "先看登录")

    _mark(client, tid, message, "alice")
    _mark(client, tid, message, "alice")
    assert _chapters(client, tid) == [message]

    r = client.delete(
        f"/topics/{tid}/chapters/{message}", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text
    assert _chapters(client, tid) == []


def test_someone_who_has_not_joined_cannot_mark_a_channel(client):
    tid = _setup(client)
    message = _say(client, tid, "alice", "先看登录")

    assert _mark(client, tid, message, "bob").status_code == 403
    assert _chapters(client, tid) == []


def test_nothing_is_marked_in_an_archived_channel(client):
    tid = _setup(client)
    message = _say(client, tid, "alice", "先看登录")
    r = client.post(f"/topics/{tid}/archive", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text

    assert _mark(client, tid, message, "alice").status_code == 403
    assert _chapters(client, tid) == []


def test_a_reply_in_a_thread_is_not_a_chapter_of_the_channel(client):
    tid = _setup(client)
    message = _say(client, tid, "alice", "预览在微信里打不开")
    r = client.post(f"/blocks/{message}/thread", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    reply = _say(client, r.json()["data"]["id"], "alice", "我看看")

    assert _mark(client, tid, reply, "alice").status_code == 404
    assert _chapters(client, tid) == []


def test_a_task_has_chapters_of_its_own(client):
    tid = _setup(client)
    task = open_task(client, tid)
    said = _say(client, task["id"], "bob", "从登录页开始")
    channel_said = _say(client, tid, "alice", "主线上的话")

    # bob has not joined the channel, but a task is spoken in by everyone in
    # the project: he may organize it.
    assert _mark(client, task["id"], said, "bob").status_code == 200

    assert _chapters(client, task["id"], who="alice") == [said]
    assert _chapters(client, tid) == []
    assert _mark(client, tid, channel_said, "alice").status_code == 200
    assert _chapters(client, tid) == [channel_said]
    assert _chapters(client, task["id"]) == [said]
