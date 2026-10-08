"""A person's message goes in through the same door an agent's does.

`POST /topics/{id}/messages` is the only way a message enters a room. What a
person used to get from sending over the room's socket they get from this
POST: the message on everyone's timeline at once, the people it names told,
the teammate it names woken, a retry that lands once, and messages that land
in the order they were sent.
"""

import uuid

from tests.conftest import wait_work_idle
from tests.integration.conftest import (
    in_thread,
    join_project_team,
    post_message,
    post_project,
    room_agent_seat,
    room_socket,
    session_auth_headers,
)
from tests.support.quoted_context import slide_quote


def test_a_retried_send_returns_the_first_saved_quote(client, stub_hooks):
    _, topic_id = _room(client)
    agent = room_agent_seat(client, topic_id)
    started = []
    stub_hooks.on_start = lambda: started.append(1)
    request_id = str(uuid.uuid4())
    first_quote = slide_quote("  最初的页面 @评审\n")
    first = post_message(
        client,
        topic_id,
        "alice",
        {
            "content": f"<@{agent}> 解释页面",
            "request_id": request_id,
            "quoted_context": first_quote,
        },
    )
    wait_work_idle()
    again = post_message(
        client,
        topic_id,
        "alice",
        {
            "content": f"<@{agent}> 解释页面",
            "request_id": request_id,
            "quoted_context": {
                **first_quote,
                "version": "version-b",
                "text": "后来变了",
            },
        },
    )
    wait_work_idle()
    assert again["id"] == first["id"]
    assert again["meta"]["quoted_context"] == first_quote
    assert started == [1]
    saved = next(b for b in _messages(client, topic_id) if b["id"] == first["id"])
    assert saved["meta"]["quoted_context"] == first_quote


def test_oversized_combined_question_and_quote_is_refused_without_landing(client):
    _, topic_id = _room(client)
    response = client.post(
        f"/topics/{topic_id}/messages",
        headers=session_auth_headers("alice"),
        json={
            "content": "a" * 99980,
            "request_id": str(uuid.uuid4()),
            "quoted_context": slide_quote("引用文字"),
        },
    )
    assert response.status_code == 400, response.text[:1000]
    error = response.json()["error"]
    assert error["message"] == "消息和引用加起来不能超过 100000 个字符"
    assert error["i18n"] == {"key": "quoteTooLong", "params": {"limit": 100000}}
    assert _messages(client, topic_id) == []


def _room(client, owner: str = "alice", *, members: tuple[str, ...] = ()) -> tuple:
    project = post_project(client, json={"name": "P"}, owner=owner)
    project_id = project.json()["data"]["id"]
    for handle in members:
        join_project_team(client, project_id, handle)
    topic = client.post(
        "/topics",
        json={"project_id": project_id, "title": "T"},
        headers=session_auth_headers(owner),
    ).json()["data"]
    for handle in members:
        added = client.post(
            f"/topics/{topic['id']}/members",
            json={"handle": handle, "role": "member", "actor": owner},
            headers=session_auth_headers(owner),
        )
        assert added.status_code == 200, added.text
    return project_id, topic["id"]


def _until_done(ws) -> list[dict]:
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] in ("done", "error"):
            return frames


def _messages(client, topic_id: str) -> list[dict]:
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    return [b for b in blocks if b["kind"] == "message"]


def test_a_persons_message_is_live_for_the_room_and_wakes_who_it_names(
    client, stub_hooks
):
    _, room = _room(client)
    agent = room_agent_seat(client, room)
    # 芝士 is called in a 支线: everyone watching it sees the message there.
    topic_id = in_thread(client, room, "alice")
    request_id = str(uuid.uuid4())
    with room_socket(client, topic_id, "alice") as ws:
        stored = post_message(
            client,
            topic_id,
            "alice",
            {"content": "@芝士 看一下这个", "request_id": request_id},
        )
        frames = _until_done(ws)

    # The reply to the POST is the stored message, carrying the sender's own id
    # for it, so the sender's pending copy can be settled from the reply alone.
    assert stored["author"] == "alice"
    assert stored["content"] == f"<@{agent}> 看一下这个"
    assert stored["meta"]["client_id"] == request_id
    # Everyone watching the room gets the same block, as a person's message.
    assert frames[0]["type"] == "user_block"
    assert frames[0]["block"]["id"] == stored["id"]
    # The teammate it names is woken, answers, and marks it seen.
    assert "turn_started" in [f["type"] for f in frames]
    assert "看一下这个" in (stub_hooks.last_prompt or "")
    seen = [f for f in frames if f["type"] == "reaction"]
    assert seen and seen[0]["block_id"] == stored["id"]


def test_a_message_that_names_nobody_wakes_nobody(client, stub_hooks):
    _, topic_id = _room(client)
    with room_socket(client, topic_id, "alice") as ws:
        post_message(client, topic_id, "alice", {"content": "我们今晚开会"})
        frames = _until_done(ws)
    assert "turn_started" not in [f["type"] for f in frames]
    assert stub_hooks.last_prompt is None


def test_naming_a_person_tells_them(client):
    project_id, topic_id = _room(client, members=("bob",))
    post_message(client, topic_id, "alice", {"content": "<@bob> 帮我看下数据"})

    alerts = client.get(
        f"/projects/{project_id}/alerts", headers=session_auth_headers("bob")
    ).json()["data"]["data"]
    assert len(alerts) == 1


def test_a_retried_send_lands_once_and_wakes_once(client, stub_hooks):
    _, room = _room(client)
    topic_id = in_thread(client, room, "alice")
    started = []
    stub_hooks.on_start = lambda: started.append(1)
    request_id = str(uuid.uuid4())
    body = {"content": "@芝士 只说一次", "request_id": request_id}
    with room_socket(client, topic_id, "alice") as ws:
        first = post_message(client, topic_id, "alice", body)
        _until_done(ws)
    again = post_message(client, topic_id, "alice", body)

    assert again["id"] == first["id"]
    assert [m["id"] for m in _messages(client, topic_id) if m["author"] == "alice"] == [
        first["id"]
    ]
    assert started == [1]


def test_messages_land_in_the_order_they_were_sent(client):
    _, topic_id = _room(client)
    for text in ("第一句", "第二句", "第三句"):
        post_message(client, topic_id, "alice", {"content": text})
    assert [m["content"] for m in _messages(client, topic_id)] == [
        "第一句",
        "第二句",
        "第三句",
    ]


def test_a_reply_threads_under_its_parent(client):
    _, topic_id = _room(client)
    parent = post_message(client, topic_id, "alice", {"content": "根消息"})
    child = post_message(
        client, topic_id, "alice", {"content": "回复", "reply_to": parent["id"]}
    )
    assert child["reply_to"] == parent["id"]


def test_an_image_alone_is_a_message(client):
    _, topic_id = _room(client)
    stored = post_message(
        client,
        topic_id,
        "alice",
        {"attachments": [{"path": "room/shot.png", "mime": "image/png"}]},
    )
    assert stored["kind"] == "attachment"
    assert stored["content"] == "room/shot.png"


def test_an_empty_message_is_refused(client):
    _, topic_id = _room(client)
    sent = client.post(
        f"/topics/{topic_id}/messages",
        json={"content": "  ", "request_id": str(uuid.uuid4())},
        headers=session_auth_headers("alice"),
    )
    assert sent.status_code == 422
    assert _messages(client, topic_id) == []
