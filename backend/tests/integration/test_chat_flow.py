"""End-to-end Phase 0 flow over HTTP + WebSocket (with the stub agent)."""

import asyncio
import uuid

from app.domain.identity.handles import topic_agent_handle
from app.domain.memory.models import MemoryScope
from app.domain.memory.store import DbMemoryStore
from tests.integration.conftest import chat_ws_url, session_auth_headers


def _create_project_and_topic(client, owner: str = "user-1") -> tuple[str, str]:
    pr = client.post("/api/projects", json={"name": "Demo", "owner_handle": owner})
    assert pr.status_code == 200
    project_id = pr.json()["data"]["id"]

    tr = client.post(
        "/api/topics",
        json={"project_id": project_id, "title": "第一个话题", "created_by": owner},
    )
    assert tr.status_code == 200
    topic_id = tr.json()["data"]["id"]
    return project_id, topic_id


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "healthy"


def test_create_and_list_project(client):
    # Both calls are authenticated, and that is the point of the test rather
    # than a formality: without `team_id` this listing means "the caller's OWN
    # projects", so an anonymous GET now answers empty (see
    # test_project_visibility.py — an unidentifiable caller used to get every
    # project on the platform, which is what a logged-in user saw whenever
    # their token lapsed). Creating anonymously would leave the project with no
    # owner and no roster, so nobody would have a claim on it either.
    headers = session_auth_headers("alice")
    client.post("/api/projects", json={"name": "P1"}, headers=headers)
    r = client.get("/api/projects", headers=headers)
    body = r.json()
    assert body["code"] == 200
    assert body["data"]["total"] == 1
    assert body["data"]["data"][0]["name"] == "P1"


def test_create_topic_requires_existing_project(client):
    r = client.post(
        "/api/topics",
        json={
            "project_id": "00000000-0000-0000-0000-000000000000",
            "title": "x",
        },
    )
    assert r.status_code == 404


def test_blocks_empty_then_populated_after_chat(client):
    _, topic_id = _create_project_and_topic(client)

    r = client.get(f"/api/topics/{topic_id}/blocks")
    assert r.json()["data"]["total"] == 0

    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "你好芝士", "summon": True})
        frames = _drain_until_done(ws)

    types = [f["type"] for f in frames]
    # Slack-style: no token deltas — the platform ✅-acks the summoning message,
    # announces the working turn (正在思考 for every open client), then 芝士's
    # reply lands as one complete message block.
    assert types == ["user_block", "reaction", "turn_active", "assistant_block", "done"]

    ack = next(f for f in frames if f["type"] == "reaction")
    agent = topic_agent_handle(uuid.UUID(topic_id))
    assert ack["reactions"] == [{"emoji": "✅", "count": 1, "authors": [agent]}]

    assistant = next(f for f in frames if f["type"] == "assistant_block")["block"]
    assert assistant["content"] == "Hello world"
    assert assistant["author_type"] == "ai"

    user = next(f for f in frames if f["type"] == "user_block")["block"]
    assert user["content"] == "你好芝士"
    assert user["author_type"] == "human"

    # Persisted: two blocks now exist in timeline order.
    r = client.get(f"/api/topics/{topic_id}/blocks")
    blocks = r.json()["data"]["data"]
    assert [b["author_type"] for b in blocks] == ["human", "ai"]


def test_session_id_persisted_for_resume(client):
    _, topic_id = _create_project_and_topic(client)
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "hi", "summon": True})
        _drain_until_done(ws)

    # Second turn should resume with the captured session id.
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "again", "summon": True})
        _drain_until_done(ws)


def test_memory_injected_into_system_prompt(client, stub_agent):
    project_id, topic_id = _create_project_and_topic(client)

    # Seed a project memory fact.
    async def _seed() -> None:
        async with client.test_factory() as session:
            await DbMemoryStore(session).remember(
                MemoryScope.project, project_id, "项目用 FastAPI 写后端"
            )
            await session.commit()

    asyncio.run(_seed())

    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "技术栈是什么", "summon": True})
        _drain_until_done(ws)

    assert stub_agent.last_system_prompt is not None
    assert "项目用 FastAPI 写后端" in stub_agent.last_system_prompt


def test_empty_content_rejected(client):
    _, topic_id = _create_project_and_topic(client)
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "   "})
        frame = ws.receive_json()
        assert frame["type"] == "error"


def test_message_without_summon_does_not_invoke_cheese(client):
    """Default human-to-human: posting without @芝士 stays quiet (spec C3)."""
    _, topic_id = _create_project_and_topic(client)
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "队友我们今晚开会"})
        frames = _drain_until_done(ws)

    types = [f["type"] for f in frames]
    assert types == ["user_block", "done"]  # no ✅ ack / assistant_block

    blocks = client.get(f"/api/topics/{topic_id}/blocks").json()["data"]["data"]
    assert [b["author_type"] for b in blocks] == ["human"]  # only the human msg


def test_unsummoned_messages_reach_next_summon_with_labels(stub_agent, client):
    # spec §7.1: messages posted without @芝士 are still seen on the next summon,
    # each tagged with who said it (§8.4 multi-person disambiguation).
    # Two speakers means two sockets: authorship is pinned to the connection's
    # token, so one socket can only ever speak as one person.
    _, topic_id = _create_project_and_topic(client, owner="alice")
    client.post(
        f"/api/topics/{topic_id}/members",
        json={"handle": "bob", "role": "member", "actor": "alice"},
    )
    with client.websocket_connect(chat_ws_url(topic_id, "alice")) as ws:
        ws.send_json({"type": "message", "content": "先随便说一句", "summon": False})
        quiet = _drain_until_done(ws)
        assert [f["type"] for f in quiet] == ["user_block", "done"]  # 芝士 quiet

    with client.websocket_connect(chat_ws_url(topic_id, "bob")) as ws:
        ws.send_json({"type": "message", "content": "再补一句", "summon": False})
        _drain_until_done(ws)

    with client.websocket_connect(chat_ws_url(topic_id, "alice")) as ws:
        ws.send_json({"type": "message", "content": "芝士看看", "summon": True})
        _drain_until_done(ws)

    prompt = stub_agent.last_prompt or ""
    assert "[alice]: 先随便说一句" in prompt
    assert "[bob]: 再补一句" in prompt
    assert "[alice]: 芝士看看" in prompt


def _drain_until_done(ws) -> list[dict]:
    frames: list[dict] = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] in ("done", "error"):
            break
    return frames


def test_debug_turns_records_lifecycle(client):
    """可 debug: /debug/turns exposes each turn's lifecycle summary (status,
    timings, tool counts) without grepping logs."""
    _, topic_id = _create_project_and_topic(client)
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "你好", "summon": True})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass

    turns = client.get("/debug/turns").json()["data"]
    assert turns, "at least the turn we just ran"
    t = turns[0]
    assert t["topic_id"] == topic_id
    assert t["status"] == "done"
    assert t["duration_s"] is not None
    assert t["first_output_s"] is not None  # the assistant message was observed
