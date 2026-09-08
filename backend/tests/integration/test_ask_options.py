"""cheese ask: option questions in the chat, one-click structured answers."""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from app.domain.identity.handles import topic_agent_handle
from tests.integration.conftest import chat_ws_url


def _topic(client) -> str:
    p = client.post("/projects", json={"name": "P"}).json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T", "created_by": "user-1"},
    ).json()["data"]
    return t["id"]


def _ask(client, tid: str) -> dict:
    r = client.post(
        f"/topics/{tid}/ask",
        json={"question": "分页方案选哪个？", "options": ["cursor", "pageStart"]},
    )
    assert r.status_code == 200
    return r.json()["data"]


def test_ask_creates_option_message(client):
    tid = _topic(client)
    blk = _ask(client, tid)
    # Authored by THIS topic's 分身, not the shared platform ``cheese`` account.
    assert blk["author"] == topic_agent_handle(uuid.UUID(tid))
    assert blk["kind"] == "message"
    assert blk["meta"]["options"] == ["cursor", "pageStart"]
    # It shows in the timeline like any message.
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    assert any(b["id"] == blk["id"] for b in blocks)


def test_ask_rejects_bad_option_counts(client):
    tid = _topic(client)
    r = client.post(
        f"/topics/{tid}/ask", json={"question": "q", "options": ["only-one"]}
    )
    assert r.status_code == 422
    r = client.post(
        f"/topics/{tid}/ask",
        json={"question": "q", "options": ["a", "b", "c", "d", "e"]},
    )
    assert r.status_code == 422


def test_ask_requires_a_valid_topic_scoped_credential(client):
    tid = _topic(client)
    body = {"question": "选哪个？", "options": ["a", "b"]}
    without_token = client.post(
        f"/topics/{tid}/ask",
        json=body,
        headers={"X-Cheese-Token": ""},
    )
    assert without_token.status_code == 401

    other = _topic(client)
    wrong_topic = client.post(
        f"/topics/{tid}/ask",
        json=body,
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=str(
                    client.get(f"/topics/{other}").json()["data"]["project_id"]
                ),
                topic_id=other,
            )
        },
    )
    assert wrong_topic.status_code == 403


def test_answer_records_choice_and_posts_reply(client):
    tid = _topic(client)
    blk = _ask(client, tid)

    r = client.post(
        f"/topics/blocks/{blk['id']}/answer",
        json={"option": "cursor", "author": "user-1"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["meta"]["answered"] == "cursor"
    assert data["meta"]["answered_by"] == "user-1"

    # The choice lands as the answerer's own message and summons 芝士. Drive
    # the submitted turn to completion the repo way: hold the WS open (replay
    # catches frames already published) until done/error.
    with client.websocket_connect(chat_ws_url(tid, "user-1")) as ws:
        while True:
            frame = ws.receive_json()
            if frame["type"] in ("done", "error"):
                break
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    debug = [(b["author"], b["kind"], b["content"][:30]) for b in blocks]
    assert any(b["author"] == "user-1" and b["content"] == "cursor" for b in blocks), (
        f"choice message never landed: {debug}"
    )


def test_answer_validates_option_and_single_shot(client):
    tid = _topic(client)
    blk = _ask(client, tid)

    r = client.post(
        f"/topics/blocks/{blk['id']}/answer",
        json={"option": "不存在的", "author": "user-1"},
    )
    assert r.status_code == 422

    client.post(
        f"/topics/blocks/{blk['id']}/answer",
        json={"option": "cursor", "author": "user-1"},
    )
    r = client.post(
        f"/topics/blocks/{blk['id']}/answer",
        json={"option": "pageStart", "author": "user-2"},
    )
    assert r.status_code == 422
