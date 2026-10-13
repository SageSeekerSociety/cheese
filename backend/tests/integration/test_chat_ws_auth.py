"""The chat WebSocket must never invent an author, and never write at all.

Field incident: a user's session token expired while a local agent was working
on their behalf. The socket kept accepting messages, the agent kept seeing
`user_block`/`done` frames, and the whole batch landed on the platform under
匿名者 — the sending side never saw an error. Two separate defects made that
possible:

1. a token that fails verification was treated as "no token" and downgraded to
   an anonymous actor, instead of failing the connection;
2. an unauthenticated socket took `author` from the message body, so any client
   could post as any handle.

The socket no longer carries messages at all — a message is a POST to
`/topics/{id}/messages`, authenticated per request — so the second defect has
nowhere left to live. Watching a room still has exactly three refusals —
`auth_required`, `auth_expired`, `forbidden` — and every one of them is pinned
below, because the client latches on the code: one the frontend does not
recognise is indistinguishable from a dropped connection, so it reconnects
forever and buries the reason. One connection carries every room a page
watches, and each room is judged by the credential its own subscription
carries.
"""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from tests.conftest import wait_work_idle
from tests.integration.conftest import (
    open_task,
    post_project,
    room_socket,
    session_auth_headers,
    session_token,
)


def _project_topic(client, owner: str) -> tuple[str, str]:
    p = post_project(client, json={"name": "P"}, owner=owner).json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T"},
        headers=session_auth_headers(owner),
    ).json()["data"]
    return p["id"], t["id"]


def _blocks(client, topic_id: str) -> list[dict]:
    return client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]


def test_expired_token_is_refused_not_downgraded(client):
    """The incident itself: an expired token must close the socket, and a
    message sent with it must not land."""
    _, tid = _project_topic(client, owner="alice")
    stale = session_token("alice", ttl_s=-60)

    with room_socket(client, tid, None, token=stale) as ws:
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert frame["code"] == "auth_expired"

    sent = client.post(
        f"/topics/{tid}/messages",
        json={"content": "偷偷发一条", "request_id": str(uuid.uuid4())},
        headers={"Authorization": f"Bearer {stale}"},
    )
    assert sent.status_code == 401
    assert _blocks(client, tid) == []


def test_garbage_token_is_refused(client):
    """A malformed token is the same failure as an expired one, not 'no token'."""
    _, tid = _project_topic(client, owner="alice")
    with room_socket(client, tid, None, token="not-a-jwt") as ws:
        frame = ws.receive_json()
    assert frame["type"] == "error"
    assert frame["code"] == "auth_expired"
    # The screen says it in its reader's language, from the sentence's key.
    assert frame["i18n"] == {"key": "chatSignInAgain", "params": {}}
    assert frame["message"] == "登录状态已失效，请重新登录后再发言"
    assert _blocks(client, tid) == []


def test_tokenless_caller_cannot_post_as_anyone(client):
    """No token at all → refused, so `author` in the body can't be forged."""
    _, tid = _project_topic(client, owner="alice")
    with room_socket(client, tid, None) as ws:
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert frame["code"] == "auth_required"
        assert frame["i18n"] == {"key": "chatSignInFirst", "params": {}}

    sent = client.post(
        f"/topics/{tid}/messages",
        json={
            "content": "我是 alice",
            "author": "alice",
            "request_id": str(uuid.uuid4()),
        },
    )
    assert sent.status_code == 401
    assert _blocks(client, tid) == []


def test_a_connection_that_cannot_be_identified_is_refused(client):
    """The connection itself needs a token that verifies, and says which of the
    two sign-in failures it is before closing."""
    from starlette.websockets import WebSocketDisconnect

    for url, code in (
        ("/rooms/live?token=not-a-jwt", "auth_expired"),
        ("/rooms/live", "auth_required"),
    ):
        with client.websocket_connect(url) as ws:
            frame = ws.receive_json()
            assert (frame["type"], frame["code"]) == ("error", code)
            try:
                ws.receive_json()
            except WebSocketDisconnect as closed:
                assert closed.code == 1008
            else:
                raise AssertionError("the connection stayed open")


def test_authenticated_non_member_is_refused_as_forbidden(client):
    """The third exit: a real login, but not this room's member.

    Distinct from the two auth codes on purpose — 重新登录 is useless advice
    here, and the frontend keys the banner off the code. Nothing may be posted
    either: an outsider who is merely told "no" but still writes is the same
    defect wearing a different message.
    """
    _, tid = _project_topic(client, owner="alice")

    with room_socket(client, tid, "mallory") as ws:
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert frame["code"] == "forbidden"

    sent = client.post(
        f"/topics/{tid}/messages",
        json={"content": "我也来说两句", "request_id": str(uuid.uuid4())},
        headers=session_auth_headers("mallory"),
    )
    assert sent.status_code == 403
    assert _blocks(client, tid) == []


def test_the_socket_writes_nothing(client):
    """A message frame on the socket is refused, not stored: the one way a
    message enters a room is the POST everyone uses."""
    _, tid = _project_topic(client, owner="alice")
    with room_socket(client, tid, "alice") as ws:
        ws.send_json({"type": "message", "content": "hello"})
        frame = ws.receive_json()
        assert frame["type"] == "error"
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
    assert _blocks(client, tid) == []


def test_a_member_socket_answers_the_liveness_ping(client):
    """A page waiting for 芝士 sends nothing; the ping is how it learns the
    link is still there, so it must be answered without posting anything."""
    _, tid = _project_topic(client, owner="alice")
    with room_socket(client, tid, "alice") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
    assert _blocks(client, tid) == []


def test_a_peer_that_drops_under_a_send_ends_the_socket_quietly(client, monkeypatch):
    """A browser that goes away is found out by the write that fails, and that
    write is swallowed on purpose (the relay must not die on it). The next read
    on a socket we have already closed must then end the handler, not raise: the
    RuntimeError it used to raise reached uvicorn as 「Exception in ASGI
    application」, an alert for nothing more than a tab closing."""
    from fastapi import WebSocket

    original_send = WebSocket.send
    peer_gone = False

    async def send_to_a_dropped_peer(self, message):
        if peer_gone and message["type"] == "websocket.send":

            async def fail(_message):
                raise OSError("Broken pipe")

            self._send = fail
        await original_send(self, message)

    monkeypatch.setattr(WebSocket, "send", send_to_a_dropped_peer)
    _, tid = _project_topic(client, owner="alice")
    with room_socket(client, tid, "alice") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        peer_gone = True
        # The pong for this one finds the peer gone. Leaving the block then
        # re-raises whatever the handler raised, which must be nothing.
        ws.send_json({"type": "ping"})
    assert _blocks(client, tid) == []


def test_a_refused_message_says_why(client, monkeypatch):
    """A message the platform refuses comes back with the reason, so the sender's
    copy can show it instead of waiting for an echo that will never come."""
    from app.core.errors import ForbiddenError
    from app.domain.agent.runtime import AgentWorkRunner

    _, tid = _project_topic(client, owner="alice")

    async def fail(*args, **kwargs):
        raise ForbiddenError("房间已关闭")

    monkeypatch.setattr(AgentWorkRunner, "receive_message", fail)
    sent = client.post(
        f"/topics/{tid}/messages",
        json={"content": "hello", "request_id": str(uuid.uuid4())},
        headers=session_auth_headers("alice"),
    )
    assert sent.status_code == 403
    assert sent.json()["error"]["message"] == "房间已关闭"


def _task(client, room_id: str) -> str:
    """One of the room's tasks. Its session's turns go out on the task's own
    channel — the task id."""
    task = open_task(client, room_id, "子活")
    wait_work_idle()
    return task["id"]


def test_a_tasks_channel_refuses_someone_outside_its_room(client):
    """A task's channel carries its session's events and checklist — the room's
    work. It is not a room, so the room check has to be the task's room's:
    an outsider holding the task id (it sits in every `?card=` link) is refused
    exactly as on the room's own channel."""
    _, room = _project_topic(client, owner="alice")
    card = _task(client, room)

    with room_socket(client, card, "mallory") as ws:
        frame = ws.receive_json()
    assert frame["type"] == "error"
    assert frame["code"] == "forbidden"


def test_a_member_watching_a_task_sees_its_workers_checklist(client):
    """The task view's live checklist: the room's member subscribes to the
    task's channel and its own session's `todo_write` arrives there."""
    project, room = _project_topic(client, owner="alice")
    card = _task(client, room)
    agent = {"X-Cheese-Token": mint_scoped_token(project_id=project, topic_id=card)}

    with room_socket(client, card, "alice") as ws:
        # The pong says the subscription is live; the task's own session may
        # already be talking on the channel ahead of it.
        ws.send_json({"type": "ping"})
        while ws.receive_json()["type"] != "pong":
            pass
        response = client.put(
            f"/topics/{card}/progress",
            json={"todos": [{"content": "改接口", "status": "in_progress"}]},
            headers=agent,
        )
        assert response.status_code == 200, response.text
        frame = ws.receive_json()
        while frame["type"] != "todo":
            frame = ws.receive_json()
    assert [(i["subject"], i["status"]) for i in frame["items"]] == [
        ("改接口", "in_progress")
    ]


def _said(client, topic_id: str, text: str) -> None:
    sent = client.post(
        f"/topics/{topic_id}/messages",
        json={"content": text, "request_id": str(uuid.uuid4())},
        headers=session_auth_headers("alice"),
    )
    assert sent.status_code == 200, sent.text


def _until(ws, predicate) -> dict:
    while True:
        frame = ws.receive_json()
        if predicate(frame):
            return frame


def test_one_connection_carries_each_room_to_its_own_watcher(client):
    """A page watches several rooms on one connection: what lands in a room
    arrives tagged with that room, a room no longer watched sends nothing more,
    and a room this person may not see is refused without costing the others."""
    pid, first = _project_topic(client, owner="alice")
    second = client.post(
        "/topics",
        json={"project_id": pid, "title": "T2"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    _, elsewhere = _project_topic(client, owner="bob")

    def text_of(frame: dict) -> str | None:
        return (frame.get("block") or {}).get("content")

    with client.websocket_connect(f"/rooms/live?token={session_token('alice')}") as ws:
        for topic in (first, second, elsewhere):
            ws.send_json(
                {"type": "subscribe", "topic": topic, "token": session_token("alice")}
            )
        refused = _until(ws, lambda f: f.get("topic") == elsewhere)
        assert (refused["type"], refused["code"]) == ("error", "forbidden")
        assert _until(ws, lambda f: f.get("topic") == elsewhere)["type"] == "closed"
        for topic in (first, second):
            ws.send_json({"type": "ping", "topic": topic})
            _until(ws, lambda f, t=topic: f == {"type": "pong", "topic": t})

        _said(client, second, "第二个房间的话")
        landed = _until(ws, lambda f: f.get("type") == "user_block")
        assert (landed["topic"], text_of(landed)) == (second, "第二个房间的话")

        ws.send_json({"type": "unsubscribe", "topic": second})
        _said(client, second, "没人在看了")
        _said(client, first, "第一个房间的话")
        landed = _until(ws, lambda f: f.get("type") == "user_block")
        assert (landed["topic"], text_of(landed)) == (first, "第一个房间的话")
