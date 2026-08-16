"""Slack-style discrete messages (no token streaming).

Tool calls are real message boundaries. The SDK adapter coalesces partial
AssistantMessages before this provider-neutral orchestration layer sees them.
"""

import pytest

from app.domain.agent.service import (
    AgentDelta,
    AgentMessage,
    AgentResult,
    AgentToolUse,
    AgentUsage,
)
from tests.conftest import StubAgent
from tests.integration.conftest import chat_ws_url


class MultiMessageAgent(StubAgent):
    """Two message boundaries with a tool call in between — the shape a real
    SDK turn produces (AssistantMessage / ToolUse / AssistantMessage). The
    result text repeats the LAST message, exactly like the real ResultMessage."""

    async def stream_reply(
        self,
        *,
        prompt,
        system_prompt,
        cwd,
        resume_session_id,
        sandbox=None,
        allowed_tools=None,
        **_,
    ):
        self.last_system_prompt = system_prompt
        self.last_prompt = prompt
        yield AgentDelta(text="我先")  # stray deltas must NOT reach the chat
        yield AgentMessage(text="我先查一下代码，稍等")
        yield AgentToolUse(name="Grep", input={"pattern": "TODO"})
        yield AgentMessage(text="查完了：一共 3 处 TODO")
        yield AgentResult(
            text="查完了：一共 3 处 TODO",
            session_id="sess-multi-1",
            usage=AgentUsage(model="stub", input_tokens=5, output_tokens=5),
        )


@pytest.fixture
def stub_agent() -> MultiMessageAgent:
    # Overrides conftest's stub_agent for this module; `client` picks it up.
    return MultiMessageAgent()


def _run_turn(client) -> tuple[str, list[dict]]:
    p = client.post("/api/projects", json={"name": "P"}).json()["data"]
    t = client.post(
        "/api/topics",
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
        "assistant_block",
        "tool",
        "assistant_block",
        "done",
    ]

    user_block = next(f for f in frames if f["type"] == "user_block")["block"]
    first, second = [f["block"] for f in frames if f["type"] == "assistant_block"]
    assert first["content"] == "我先查一下代码，稍等"
    assert second["content"] == "查完了：一共 3 处 TODO"
    assert first["id"] != second["id"]
    # The first message threads under the summoning message; follow-ups stand
    # alone (Slack-style consecutive sends).
    assert first["reply_to"] == user_block["id"]
    assert second["reply_to"] is None


def test_result_text_is_not_duplicated_as_extra_block(client):
    topic_id, _frames = _run_turn(client)
    blocks = client.get(f"/api/topics/{topic_id}/blocks").json()["data"]["data"]
    ai_messages = [
        b for b in blocks if b["author_type"] == "ai" and b["kind"] == "message"
    ]
    # Exactly the two boundary messages — the final result text (which repeats
    # the last message) must not land a third time.
    assert [b["content"] for b in ai_messages] == [
        "我先查一下代码，稍等",
        "查完了：一共 3 处 TODO",
    ]


def test_plain_result_only_agent_still_lands_one_message(client, stub_agent):
    """Fallback: a provider with no message boundaries (conftest's StubAgent
    shape — deltas + result only) still lands its reply as one block."""

    async def _plain(**_):
        yield AgentDelta(text="Hello ")
        yield AgentResult(text="Hello world", session_id="s1", usage=None)

    stub_agent.stream_reply = lambda **kw: _plain(**kw)  # type: ignore[method-assign]
    topic_id, frames = _run_turn(client)
    assistant = [f["block"] for f in frames if f["type"] == "assistant_block"]
    assert len(assistant) == 1
    assert assistant[0]["content"] == "Hello world"
