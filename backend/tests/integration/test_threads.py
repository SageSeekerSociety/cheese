"""支线: the replies under a message in a channel.

A person calling 芝士 in a channel's main line is answered in that message's
支线, never in the main line. Anyone can reply under any message, and the
支线 they open is the message's one 支线. Only the people who took part are
told of new replies, and only of what people said there.
"""

import asyncio
import uuid

from app.domain.agent.models import AgentTurn
from tests.integration.conftest import (
    join_project_team,
    post_message,
    post_project,
    room_socket,
    session_auth_headers,
)
from tests.support.session_records import assistant_texts, records, wait_for_record


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


def test_calling_cheese_in_the_main_line_runs_in_that_messages_thread(
    client,
):
    _project, room = _channel(client)
    asked = post_message(client, room, "alice", {"content": "@芝士 首页要改哪些地方？"})

    # The question has a 支线, and the session runs in it.
    thread = _open(client, asked["id"])
    output = wait_for_record(
        client, thread["id"], lambda record: record.get("type") == "assistant"
    )
    assert assistant_texts(records(client, thread["id"])) == ["Hello world"]

    # The main line carries the question, not the answer.
    main = _blocks(client, room)
    assert [b["id"] for b in main] == [asked["id"]]

    async def replied_to() -> str:
        async with client.test_factory() as session:
            turn = await session.get(AgentTurn, uuid.UUID(output["turn_id"]))
            assert turn is not None
            return str(turn.reply_to)

    assert asyncio.run(replied_to()) == asked["id"]
    assert records(client, room) == []


def _until(ws, predicate) -> dict:
    """读到第一条满足 ``predicate`` 的帧（它之前的都丢掉）。"""
    while True:
        frame = ws.receive_json()
        if predicate(frame):
            return frame


def test_the_main_line_is_told_it_is_answering_the_moment_the_thread_opens(client):
    """@ 芝士 在频道主线上：房间会收到「有人在答」，答在哪儿也说得准。

    从一次真的发出（`receive_message` 解析出点名的是哪个席位、把消息落进哪条
    支线）走到房间那一帧，整条线在这儿锁住 —— 席位解析错了就一条支线都开不
    起来，这一帧也就永远不来。
    """
    _project, room = _channel(client)
    with room_socket(client, room, "alice") as ws:
        asked = post_message(
            client, room, "alice", {"content": "@芝士 首页要改哪些地方？"}
        )
        heard = _until(ws, lambda f: f["type"] == "thread_activity" and f["active"])
    # 谁在答：芝士坐的那个席位。答在哪儿：这条消息的支线（`_open` 是幂等的，
    # 拿回来的就是它）。
    assert heard["member"]
    assert heard["thread_id"] == _open(client, asked["id"])["id"]


def test_the_main_line_shows_how_a_thread_grew(client):
    _project, room = _channel(client)
    said = post_message(client, room, "alice", {"content": "下午三点评审"})
    thread = _open(client, said["id"])
    post_message(client, thread["id"], "alice", {"content": "改到四点"})

    under = next(b for b in _blocks(client, room) if b["id"] == said["id"])
    assert under["thread"]["id"] == thread["id"]
    assert under["thread"]["reply_count"] == 1
    assert under["thread"]["last_reply"]["content"] == "改到四点"
    # An ordinary message carries no step list.
    assert under["thread"]["last_reply"]["checklist"] is None


def test_the_line_under_a_message_gets_the_step_list_of_the_last_reply(client):
    """The line says one step of the last reply. A step list's text folds to a
    row of marks by the time it gets there — the breaks that tell one step from
    the next are gone — so the list itself rides along with the reply."""
    _project, room = _channel(client)
    said = post_message(client, room, "alice", {"content": "首页要改哪些地方？"})
    thread = _open(client, said["id"])
    written = client.put(
        f"/topics/{thread['id']}/progress",
        json={
            "todos": [
                {"content": "读代码", "status": "completed"},
                {"content": "改摘要行", "status": "in_progress"},
            ]
        },
        headers=session_auth_headers("alice"),
    )
    assert written.status_code == 200, written.text

    under = next(b for b in _blocks(client, room) if b["id"] == said["id"])
    assert under["thread"]["last_reply"]["checklist"] == {
        "items": [
            {"id": "1", "subject": "读代码", "status": "completed"},
            {"id": "2", "subject": "改摘要行", "status": "in_progress"},
        ],
        "result": None,
    }


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
        (row,) = r.json()["data"]["open"]
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
