"""Structured tool-event persistence: each 现场 event block stores {tool, arg,
platform} in `meta` so the UI translates and colors dots at DISPLAY time —
including tools missing from today's verb table, and subagent-nested calls
(which arrive through the same AgentToolUse path with the same field shapes)."""

import pytest

from app.domain.agent.service import (
    AgentDelta,
    AgentResult,
    AgentToolUse,
    AgentUsage,
)
from tests.conftest import StubAgent
from tests.integration.conftest import chat_ws_url


class ToolStubAgent(StubAgent):
    """Streams a mix of platform / plain / unmapped tool calls, then a result."""

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
        yield AgentToolUse(name="Grep", input={"pattern": "TODO", "path": "src"})
        yield AgentToolUse(name="mcp__cheese__update_doc", input={"content": "# 文档"})
        yield AgentToolUse(name="Bash", input={"command": 'cheese title "新标题"'})
        yield AgentToolUse(name="Bash", input={"command": "ls -la"})
        yield AgentToolUse(name="FutureTool", input={"x": 1})
        yield AgentDelta(text="done")
        yield AgentResult(
            text="done",
            session_id="sess-tools-1",
            usage=AgentUsage(model="stub", input_tokens=1, output_tokens=1),
        )


@pytest.fixture
def stub_agent() -> ToolStubAgent:
    # Overrides conftest's stub_agent for this module; `client` picks it up.
    return ToolStubAgent()


def _chat(client, topic_id: str) -> None:
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "hi", "summon": True})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass


def test_event_blocks_persist_structured_meta(client):
    p = client.post("/api/projects", json={"name": "P"}).json()["data"]
    t = client.post(
        "/api/topics",
        json={"project_id": p["id"], "title": "话题", "created_by": "user-1"},
    ).json()["data"]
    _chat(client, t["id"])

    tr = client.get(f"/api/topics/{t['id']}/transcript").json()["data"]["data"]
    by_tool = {b["meta"]["tool"]: b for b in tr if (b.get("meta") or {}).get("tool")}

    # Plain work → neutral dot; verb/arg live in meta for display-time labels.
    grep = by_tool["Grep"]
    assert grep["meta"] == {"tool": "Grep", "arg": "TODO", "platform": False}
    assert grep["content"] == "搜内容\nTODO"  # human-readable fallback text

    # cheese MCP tool → platform (amber dot); name stored mcp-prefix-stripped.
    doc = by_tool["update_doc"]
    assert doc["meta"]["platform"] is True
    assert doc["meta"]["arg"] == "# 文档"

    # Bash running the cheese CLI → platform; plain Bash → not.
    bash_events = [b for b in tr if (b.get("meta") or {}).get("tool") == "Bash"]
    platforms = {b["meta"]["arg"]: b["meta"]["platform"] for b in bash_events}
    assert platforms['cheese title "新标题"'] is True
    assert platforms["ls -la"] is False

    # A tool missing from the verb table: content bakes the raw name (legacy
    # fallback), but meta still lets a NEWER frontend table translate it.
    fut = by_tool["FutureTool"]
    assert fut["content"] == "FutureTool"
    assert fut["meta"] == {"tool": "FutureTool", "platform": False}
