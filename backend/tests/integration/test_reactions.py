"""Emoji reactions (Slack semantics): the toggle API, the aggregated GET
payload, the live WS broadcast, and 芝士's deterministic ✅ receipt on the
message that summoned it (a platform action, never AI-generated text)."""

import asyncio
import uuid

import pytest

from app.domain.agent.chat import ChatService
from app.domain.identity.handles import topic_agent_handle
from tests.conftest import StubAgent
from tests.integration.conftest import chat_ws_url


def _create_topic(client, owner: str = "alice") -> str:
    p = client.post("/api/projects", json={"name": "P"}).json()["data"]
    t = client.post(
        "/api/topics",
        json={"project_id": p["id"], "title": "话题", "created_by": owner},
    ).json()["data"]
    return t["id"]


def _post_message(client, topic_id: str, content: str, author: str) -> str:
    """Post a plain (unsummoned) message as `author`; returns the new block's id."""
    with client.websocket_connect(chat_ws_url(topic_id, author)) as ws:
        ws.send_json({"type": "message", "content": content, "summon": False})
        block_id = ""
        while True:
            frame = ws.receive_json()
            if frame["type"] == "user_block":
                block_id = frame["block"]["id"]
            if frame["type"] in ("done", "error"):
                break
    assert block_id
    return block_id


def _toggle(client, block_id: str, emoji: str, author: str) -> dict:
    r = client.post(
        f"/api/blocks/{block_id}/reactions", json={"emoji": emoji, "author": author}
    )
    assert r.status_code == 200
    return r.json()["data"]


def _block_reactions(client, topic_id: str, block_id: str) -> list[dict]:
    blocks = client.get(f"/api/topics/{topic_id}/blocks").json()["data"]["data"]
    return next(b for b in blocks if b["id"] == block_id)["reactions"]


def test_toggle_adds_then_removes(client):
    topic_id = _create_topic(client)
    block_id = _post_message(client, topic_id, "大家看看这个", "alice")

    out = _toggle(client, block_id, "👍", "bob")
    assert out["toggled"] == "added"
    assert out["reactions"] == [{"emoji": "👍", "count": 1, "authors": ["bob"]}]
    assert _block_reactions(client, topic_id, block_id) == out["reactions"]

    # Same (emoji, author) again → removed (Slack toggle), aggregate empties.
    out = _toggle(client, block_id, "👍", "bob")
    assert out["toggled"] == "removed"
    assert out["reactions"] == []
    assert _block_reactions(client, topic_id, block_id) == []


def test_aggregate_groups_by_emoji_with_authors(client):
    topic_id = _create_topic(client)
    block_id = _post_message(client, topic_id, "方案 A 还是 B？", "alice")

    _toggle(client, block_id, "👍", "bob")
    _toggle(client, block_id, "👍", "carol")
    out = _toggle(client, block_id, "🎉", "bob")

    assert out["reactions"] == [
        {"emoji": "👍", "count": 2, "authors": ["bob", "carol"]},
        {"emoji": "🎉", "count": 1, "authors": ["bob"]},
    ]


def test_reaction_broadcasts_live_ws_frame(client):
    topic_id = _create_topic(client)
    block_id = _post_message(client, topic_id, "看这条", "alice")

    with client.websocket_connect(chat_ws_url(topic_id, "alice")) as ws:
        out = _toggle(client, block_id, "👀", "bob")
        frame = ws.receive_json()
    assert frame == {
        "type": "reaction",
        "block_id": block_id,
        "reactions": out["reactions"],
    }


def test_reaction_on_missing_block_is_404(client):
    r = client.post(
        f"/api/blocks/{uuid.uuid4()}/reactions",
        json={"emoji": "👍", "author": "bob"},
    )
    assert r.status_code == 404


def test_summon_gets_cheese_check_receipt(client):
    """芝士 collega-style ack: the summoning message gets a ✅ by "cheese" the
    moment the turn starts — broadcast live and persisted on the block."""
    topic_id = _create_topic(client)
    with client.websocket_connect(chat_ws_url(topic_id, "alice")) as ws:
        ws.send_json({"type": "message", "content": "芝士帮我看看", "summon": True})
        frames = []
        while True:
            frames.append(ws.receive_json())
            if frames[-1]["type"] in ("done", "error"):
                break

    user_block = next(f for f in frames if f["type"] == "user_block")["block"]
    ack = next(f for f in frames if f["type"] == "reaction")
    # The ✅ lands BEFORE any assistant message (it's the receipt, not the reply).
    assert frames.index(ack) < frames.index(
        next(f for f in frames if f["type"] == "assistant_block")
    )
    assert ack["block_id"] == user_block["id"]
    agent = topic_agent_handle(uuid.UUID(topic_id))
    assert ack["reactions"] == [{"emoji": "✅", "count": 1, "authors": [agent]}]
    assert _block_reactions(client, topic_id, user_block["id"]) == ack["reactions"]


def test_unsummoned_message_gets_no_receipt(client):
    topic_id = _create_topic(client)
    block_id = _post_message(client, topic_id, "队友闲聊一句", "alice")
    assert _block_reactions(client, topic_id, block_id) == []


@pytest.mark.anyio
async def test_resume_turn_adds_no_receipt(client, tmp_path):
    """A system-initiated turn (自动续跑 / nudge) has no human summon message —
    nothing gets ✅-acked and no reaction frame is emitted."""
    # Use the shared Postgres-backed factory: the merged Base.metadata now carries
    # main's PG-only sequences (e.g. discussion_seq), which SQLite cannot create.
    factory = client.test_factory  # type: ignore[attr-defined]

    svc = ChatService(
        session_factory=factory,
        agent=StubAgent(),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    from app.domain.project.services import ProjectService
    from app.domain.topic.services import TopicService

    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id = topic.id
        await session.commit()

    frames = [
        f
        async for f in svc.converse(
            topic_id=topic_id,
            author="system",
            content="接着跑",
            summon=True,
            is_resume=True,
            resume_reason="测试",
        )
    ]
    assert all(f["type"] != "reaction" for f in frames)

    from sqlalchemy import select

    from app.domain.block.models import BlockReaction

    async with factory() as session:
        rows = (await session.scalars(select(BlockReaction))).all()
    assert rows == []
    await asyncio.sleep(0)  # let any stray tasks settle
