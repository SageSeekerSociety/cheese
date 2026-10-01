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
nowhere left to live. The connect check still has exactly three exits —
`auth_required`, `auth_expired`, `forbidden` — and every one of them is pinned
below, because the client latches on the code: one the frontend does not
recognise is indistinguishable from a dropped connection, so it reconnects
forever and buries the reason.
"""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from tests.conftest import wait_work_idle
from tests.integration.conftest import (
    chat_ws_url,
    post_project,
    session_auth_headers,
    session_token,
)


def _project_topic(client, owner: str) -> tuple[str, str]:
    p = post_project(client, json={"name": "P", "owner_handle": owner}).json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T", "created_by": owner},
    ).json()["data"]
    return p["id"], t["id"]


def _blocks(client, topic_id: str) -> list[dict]:
    return client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]


def test_expired_token_is_refused_not_downgraded(client):
    """The incident itself: an expired token must close the socket, and a
    message sent with it must not land."""
    _, tid = _project_topic(client, owner="alice")
    stale = session_token("alice", ttl_s=-60)

    with client.websocket_connect(f"/topics/{tid}/chat?token={stale}") as ws:
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
    with client.websocket_connect(f"/topics/{tid}/chat?token=not-a-jwt") as ws:
        frame = ws.receive_json()
    assert frame["type"] == "error"
    assert frame["code"] == "auth_expired"
    assert _blocks(client, tid) == []


def test_tokenless_caller_cannot_post_as_anyone(client):
    """No token at all → refused, so `author` in the body can't be forged."""
    _, tid = _project_topic(client, owner="alice")
    with client.websocket_connect(f"/topics/{tid}/chat") as ws:
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert frame["code"] == "auth_required"

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


def test_authenticated_non_member_is_refused_as_forbidden(client):
    """The third exit: a real login, but not this room's member.

    Distinct from the two auth codes on purpose — 重新登录 is useless advice
    here, and the frontend keys the banner off the code. Nothing may be posted
    either: an outsider who is merely told "no" but still writes is the same
    defect wearing a different message.
    """
    _, tid = _project_topic(client, owner="alice")

    with client.websocket_connect(chat_ws_url(tid, "mallory")) as ws:
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
    with client.websocket_connect(chat_ws_url(tid, "alice")) as ws:
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
    with client.websocket_connect(chat_ws_url(tid, "alice")) as ws:
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
    with client.websocket_connect(chat_ws_url(tid, "alice")) as ws:
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
    from app.domain.agent.runtime import InProcessBroker

    _, tid = _project_topic(client, owner="alice")

    async def fail(*args, **kwargs):
        raise ForbiddenError("房间已关闭")

    monkeypatch.setattr(InProcessBroker, "receive_message", fail)
    sent = client.post(
        f"/topics/{tid}/messages",
        json={"content": "hello", "request_id": str(uuid.uuid4())},
        headers=session_auth_headers("alice"),
    )
    assert sent.status_code == 403
    assert sent.json()["error"]["message"] == "房间已关闭"


def _card(client, room_id: str) -> str:
    """One of the room's cards. A 分身 works it inside the room's session, and
    what it does goes out on the card's own channel — the card id."""
    task = client.post(
        f"/topics/{room_id}/split", json={"title": "子活", "reviewer_handle": "alice"}
    ).json()["data"]
    wait_work_idle()
    return task["id"]


def test_a_cards_channel_refuses_someone_outside_its_room(client):
    """A card's channel carries its 分身's events and checklist — the room's
    work. It is not a room, so the room check has to be the card's room's:
    an outsider holding the card id (it sits in every `?card=` link) is refused
    exactly as on the room's own channel."""
    _, room = _project_topic(client, owner="alice")
    card = _card(client, room)

    with client.websocket_connect(chat_ws_url(card, "mallory")) as ws:
        frame = ws.receive_json()
    assert frame["type"] == "error"
    assert frame["code"] == "forbidden"


def test_a_member_watching_a_card_sees_its_workers_checklist(client):
    """The card view's live checklist: the room's member subscribes to the card's
    channel and the 分身's `todo_write` arrives there."""
    project, room = _project_topic(client, owner="alice")
    card = _card(client, room)
    agent = {"X-Cheese-Token": mint_scoped_token(project_id=project, topic_id=room)}

    with client.websocket_connect(chat_ws_url(card, "alice")) as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        response = client.put(
            f"/topics/{room}/progress",
            json={
                "todos": [{"content": "改接口", "status": "in_progress"}],
                "task": card,
            },
            headers=agent,
        )
        assert response.status_code == 200, response.text
        frame = ws.receive_json()
        while frame["type"] != "todo":
            frame = ws.receive_json()
    assert [(i["subject"], i["status"]) for i in frame["items"]] == [
        ("改接口", "in_progress")
    ]
