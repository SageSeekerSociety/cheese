"""The chat WebSocket must never invent an author.

Field incident: a user's session token expired while a local agent was working
on their behalf. The socket kept accepting messages, the agent kept seeing
`user_block`/`done` frames, and the whole batch landed on the platform under
匿名者 — the sending side never saw an error. Two separate defects made that
possible, and both are asserted here against real HTTP/WS + real blocks:

1. a token that fails verification was treated as "no token" and downgraded to
   an anonymous actor, instead of failing the connection;
2. an unauthenticated socket took `author` from the message body, so any client
   could post as any handle.

The connect check has exactly three exits — `auth_required`, `auth_expired`,
`forbidden` — and every one of them is pinned below, because the client latches
on the code: one the frontend does not recognise is indistinguishable from a
dropped connection, so it reconnects forever and buries the reason.
"""

import pytest

from app.core.config import settings
from tests.integration.conftest import chat_ws_url, session_token


def _project_topic(client, owner: str) -> tuple[str, str]:
    p = client.post("/projects", json={"name": "P", "owner_handle": owner}).json()[
        "data"
    ]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T", "created_by": owner},
    ).json()["data"]
    return p["id"], t["id"]


def _blocks(client, topic_id: str) -> list[dict]:
    return client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]


def test_expired_token_is_refused_not_downgraded(client):
    """The incident itself: an expired token must close the socket, not post."""
    _, tid = _project_topic(client, owner="alice")
    stale = session_token("alice", ttl_s=-60)

    with client.websocket_connect(f"/topics/{tid}/chat?token={stale}") as ws:
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert frame["code"] == "auth_expired"
        with pytest.raises(Exception):  # noqa: B017 - any close/receive failure
            ws.send_json({"type": "message", "content": "偷偷发一条", "summon": False})
            ws.receive_json()

    assert _blocks(client, tid) == []


def test_garbage_token_is_refused(client):
    """A malformed token is the same failure as an expired one, not 'no token'."""
    _, tid = _project_topic(client, owner="alice")
    with client.websocket_connect(f"/topics/{tid}/chat?token=not-a-jwt") as ws:
        frame = ws.receive_json()
    assert frame["type"] == "error"
    assert frame["code"] == "auth_expired"
    assert _blocks(client, tid) == []


def test_tokenless_socket_cannot_post_as_anyone(client):
    """No token at all → refused, so `author` in the body can't be forged."""
    _, tid = _project_topic(client, owner="alice")
    with client.websocket_connect(f"/topics/{tid}/chat") as ws:
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert frame["code"] == "auth_required"
        with pytest.raises(Exception):  # noqa: B017 - any close/receive failure
            ws.send_json(
                {"type": "message", "content": "我是 alice", "author": "alice"}
            )
            ws.receive_json()

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
        with pytest.raises(Exception):  # noqa: B017 - any close/receive failure
            ws.send_json({"type": "message", "content": "我也来说两句"})
            ws.receive_json()

    assert _blocks(client, tid) == []


def test_authenticated_socket_still_posts(client):
    """The fix must not cost the real path: a member's token posts as themself."""
    _, tid = _project_topic(client, owner="alice")
    with client.websocket_connect(chat_ws_url(tid, "alice")) as ws:
        ws.send_json({"type": "message", "content": "hello", "author": "mallory"})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass

    posted = [b for b in _blocks(client, tid) if b["content"] == "hello"]
    assert posted and all(b["author"] == "alice" for b in posted)


def test_anonymous_escape_hatch_never_covers_a_bad_token(client, monkeypatch):
    """`chat_ws_allow_anonymous` re-opens the tokenless harness path only.

    A socket that presented a credential we could not verify stays refused — the
    downgrade that caused the incident is not something an operator can switch
    back on by mistake.
    """
    monkeypatch.setattr(settings, "chat_ws_allow_anonymous", True)
    _, tid = _project_topic(client, owner="alice")

    with client.websocket_connect(f"/topics/{tid}/chat") as ws:
        ws.send_json({"type": "message", "content": "harness", "author": "harness"})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass
    assert [b["author"] for b in _blocks(client, tid) if b["content"] == "harness"] == [
        "harness"
    ]

    stale = session_token("alice", ttl_s=-60)
    with client.websocket_connect(f"/topics/{tid}/chat?token={stale}") as ws:
        frame = ws.receive_json()
    assert frame["code"] == "auth_expired"
