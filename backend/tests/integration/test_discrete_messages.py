"""Execution messages are durable activity; Stop publishes the final reply."""

import uuid

import pytest

from tests.conftest import StubChannel
from tests.integration.conftest import chat_ws_url


class MultiMessageScreen(StubChannel):
    """Two message boundaries with a tool call in between — the shape a real
    session produces. The Stop text repeats the LAST message, exactly like a
    real one does."""

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        del reply
        self.starts(topic_id)
        self.acknowledges(topic_id, prompt)
        self.says(topic_id, "我先查一下代码，稍等")
        self.uses(topic_id, "Grep", pattern="TODO")
        self.says(topic_id, "查完了：一共 3 处 TODO")
        self.stops(topic_id, "查完了：一共 3 处 TODO")


@pytest.fixture
def stub_hooks() -> MultiMessageScreen:
    # Overrides conftest's stub_hooks for this module; `client` picks it up.
    return MultiMessageScreen()


def _run_turn(client) -> tuple[str, list[dict]]:
    p = client.post("/projects", json={"name": "P"}).json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "话题", "created_by": "alice"},
    ).json()["data"]
    with client.websocket_connect(chat_ws_url(t["id"], "alice")) as ws:
        ws.send_json({"type": "message", "content": "帮我看看", "summon": True})
        frames = []
        while True:
            frames.append(ws.receive_json())
            if frames[-1]["type"] in ("done", "error"):
                break
    return t["id"], frames


def test_each_message_boundary_lands_as_own_block(client):
    topic_id, frames = _run_turn(client)

    # No delta frames ever reach the chat; messages arrive as they complete,
    # interleaved with the tool activity that separates them.
    types = [f["type"] for f in frames]
    assert "delta" not in types
    assert types == [
        "user_block",
        "turn_started",  # explicit lifecycle for every open client
        "reaction",  # the platform's ✅ receipt on the summoning message
        "turn_started",  # again, from the session that picked the work up
        "event_block",  # execution note
        "event_block",  # the tool call, as the 现场 record of it
        "event_block",  # complete final text retained in activity
        "assistant_block",
        "done",
    ]

    user_block = next(f for f in frames if f["type"] == "user_block")["block"]
    first, second = [
        f["block"]
        for f in frames
        if f["type"] == "event_block" and f["block"]["meta"].get("progress")
    ]
    assert first["content"] == "我先查一下代码，稍等"
    assert second["content"] == "查完了：一共 3 处 TODO"
    assert first["id"] != second["id"]
    assert first["meta"]["in_room"] is False
    assert second["meta"]["in_room"] is False
    final = next(f["block"] for f in frames if f["type"] == "assistant_block")
    assert final["content"] == second["content"]
    assert first["reply_to"] == user_block["id"]
    assert final["reply_to"] == user_block["id"]


def test_result_text_is_not_duplicated_as_extra_block(client):
    topic_id, _frames = _run_turn(client)
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    ai_messages = [
        b for b in blocks if b["author_type"] == "ai" and b["kind"] == "message"
    ]
    # Only the final answer is a chat message; the earlier note stays in activity.
    assert [b["content"] for b in ai_messages] == [
        "查完了：一共 3 处 TODO",
    ]


def test_plain_result_only_agent_still_lands_one_message(client, stub_hooks):
    """Fallback: a session that never displays a message and only stops still
    lands its reply as one block."""

    def _plain(topic_id, prompt, reply):
        del reply
        stub_hooks.starts(topic_id)
        stub_hooks.acknowledges(topic_id, prompt)
        stub_hooks.stops(topic_id, "Hello world")

    stub_hooks.emit_turn = _plain  # type: ignore[method-assign]
    topic_id, frames = _run_turn(client)
    assistant = [f["block"] for f in frames if f["type"] == "assistant_block"]
    assert len(assistant) == 1
    assert assistant[0]["content"] == "Hello world"
