"""End-to-end Phase 0 flow over HTTP + WebSocket (with the stub agent)."""

import asyncio

from app.domain.memory.models import MemoryLayer, MemoryScope
from app.domain.memory.store import DbMemoryStore
from tests.integration.conftest import (
    chat_ws_url,
    room_agent_seat,
    session_auth_headers,
)


def _create_project_and_topic(client, owner: str = "user-1") -> tuple[str, str]:
    pr = client.post("/projects", json={"name": "Demo", "owner_handle": owner})
    assert pr.status_code == 200
    project_id = pr.json()["data"]["id"]

    tr = client.post(
        "/topics",
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
    client.post("/projects", json={"name": "P1"}, headers=headers)
    r = client.get("/projects", headers=headers)
    body = r.json()
    assert body["code"] == 200
    assert body["data"]["total"] == 1
    assert body["data"]["data"][0]["name"] == "P1"


def test_create_topic_requires_existing_project(client):
    r = client.post(
        "/topics",
        json={
            "project_id": "00000000-0000-0000-0000-000000000000",
            "title": "x",
        },
    )
    assert r.status_code == 404


def test_blocks_empty_then_populated_after_chat(client):
    _, topic_id = _create_project_and_topic(client)

    r = client.get(f"/topics/{topic_id}/blocks")
    assert r.json()["data"]["total"] == 0

    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "你好芝士", "summon": True})
        frames = _drain_until_done(ws)

    # Slack-style: no token deltas — the turn announces itself and terminal
    # output is retained in activity.
    #
    # One `turn_started`, not two: the platform emits its own only while no
    # session has claimed the lifecycle (`session_lifecycle` → `session_owned`),
    # and here the session claims it first.
    #
    # `reaction` (芝士's 👀) is filtered out rather than placed: it rides the
    # harness's prompt receipt, which is reported on its own task, so it has no
    # fixed position among these. test_reactions.py is where it is asserted.
    types = [f["type"] for f in frames if f["type"] != "reaction"]
    assert types == [
        "user_block",
        "turn_started",
        "event_block",
        "done",
    ]

    ack = next(f for f in frames if f["type"] == "reaction")
    agent = room_agent_seat(client, topic_id)
    assert ack["reactions"] == [{"emoji": "👀", "count": 1, "authors": [agent]}]

    assistant = next(f for f in frames if f["type"] == "event_block")["block"]
    assert assistant["content"] == "Hello world"
    assert assistant["author_type"] == "ai"

    user = next(f for f in frames if f["type"] == "user_block")["block"]
    assert user["content"] == "你好芝士"
    assert user["author_type"] == "human"
    # Explicit null distinguishes a new pending input from an unmarked legacy
    # block. A later exact receipt replaces it with the consuming turn id.
    assert user["meta"]["consumed_turn"] is None

    # Neither displayed text nor Stop publishes a chat message.
    r = client.get(f"/topics/{topic_id}/blocks")
    blocks = r.json()["data"]["data"]
    assert [b["author_type"] for b in blocks] == ["human", "ai"]
    assert blocks[1]["meta"]["progress"] is True
    assert blocks[1]["meta"]["in_room"] is False
    assert not any(b["author_type"] == "ai" and b["kind"] == "message" for b in blocks)
    assert blocks[0]["meta"]["consumed_turn"]


def test_session_id_persisted_for_resume(client):
    _, topic_id = _create_project_and_topic(client)
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "hi", "summon": True})
        _drain_until_done(ws)

    # Second turn should resume with the captured session id.
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "again", "summon": True})
        _drain_until_done(ws)


def test_a_doc_edit_between_turns_reaches_the_next_turns_prompt(client, stub_hooks):
    """The whole point of writing the notice down, end to end.

    A session keeps the system prompt it was started with, so the document the
    agent is holding is the one from turn one. An edit that lands between turns
    has no running session to be pushed at — it waits on the event, and the next
    turn is handed it the way it is handed a message somebody typed.
    """
    _, topic_id = _create_project_and_topic(client)
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "开工", "summon": True})
        _drain_until_done(ws)

    doc = "# 目标\n\n做推荐\n\n## 验收标准\n\nRecall@10 > 0.15\n"
    for version, content in ((0, doc), (1, doc.replace("0.15", "0.25"))):
        assert (
            client.put(
                f"/topics/{topic_id}/doc",
                json={
                    "content": content,
                    "author": "user-1",
                    "expected_version": version,
                },
            ).status_code
            == 200
        )

    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "接着做", "summon": True})
        _drain_until_done(ws)

    said = stub_hooks.last_prompt
    assert said is not None
    assert "实况文档已被" in said and "第 2 版" in said
    assert "「验收标准」" in said
    # It locates the change without carrying it: a document pushed at a turn
    # displaces the work instead of informing it.
    assert "Recall@10 > 0.25" not in said
    # And having been read, it is not said again.
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "继续", "summon": True})
        _drain_until_done(ws)
    assert "实况文档已被" not in (stub_hooks.last_prompt or "")


def test_core_memory_is_carried_and_an_ordinary_fact_is_only_counted(
    client, stub_hooks
):
    """What a turn opens with, end to end. Core is there because it is what the
    agent must know to be itself; the rest is not, and the prompt says so — a
    prompt that looks complete is one nobody searches, and `recall` is the only
    way those facts reach a turn at all."""
    project_id, topic_id = _create_project_and_topic(client)

    async def _seed() -> None:
        async with client.test_factory() as session:
            store = DbMemoryStore(session)
            await store.remember(
                MemoryScope.project,
                project_id,
                "你是芝士，回答先给结论",
                layer=MemoryLayer.core,
            )
            await store.remember(
                MemoryScope.project, project_id, "项目用 FastAPI 写后端"
            )
            await session.commit()

    asyncio.run(_seed())

    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "技术栈是什么", "summon": True})
        _drain_until_done(ws)

    prompt = stub_hooks.last_system_prompt
    assert prompt is not None
    assert "你是芝士，回答先给结论" in prompt
    assert "项目用 FastAPI 写后端" not in prompt
    assert "记忆池里另有 **1 条**" in prompt
    assert "cheese_recall" in prompt


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
    assert types == ["user_block", "done"]  # no 👀 ack / assistant_block

    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    assert [b["author_type"] for b in blocks] == ["human"]  # only the human msg


def test_unsummoned_messages_reach_next_summon_with_labels(stub_hooks, client):
    # spec §7.1: messages posted without @芝士 are still seen on the next summon,
    # each tagged with who said it (§8.4 multi-person disambiguation).
    # Two speakers means two sockets: authorship is pinned to the connection's
    # token, so one socket can only ever speak as one person.
    _, topic_id = _create_project_and_topic(client, owner="alice")
    client.post(
        f"/topics/{topic_id}/members",
        json={"handle": "bob", "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
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

    prompt = stub_hooks.last_prompt or ""
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
    # Stamped from what the SESSION produced, not from frames crossing this
    # request: the call that starts a turn returns before 芝士 says anything, so
    # a summary fed only by that stream reports every healthy turn with the
    # exact signature of a sandbox whose hooks never arrive.
    assert t["first_output_s"] is not None
