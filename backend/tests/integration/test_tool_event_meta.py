"""Structured tool-event persistence: each 现场 event block stores {tool, arg,
platform} in `meta` so the UI translates and colors dots at DISPLAY time —
including tools missing from today's verb table, and subagent-nested calls
(which arrive through the same AgentToolUse path with the same field shapes)."""

import uuid

import pytest

from tests.conftest import StubHooksProvider
from tests.integration.conftest import chat_ws_url


class ToolScreen(StubHooksProvider):
    """A session using a mix of platform / plain / unmapped tools."""

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        del reply
        self.starts(topic_id)
        self.acknowledges(topic_id, prompt)
        self.uses(topic_id, "Grep", pattern="TODO", path="src")
        self.uses(topic_id, "mcp__cheese__update_doc", content="# 文档")
        self.uses(topic_id, "Bash", command='cheese title "新标题"')
        self.uses(topic_id, "Bash", command="ls -la")
        self.uses(topic_id, "FutureTool", x=1)
        self.stops(topic_id, "done")


@pytest.fixture
def stub_hooks() -> ToolScreen:
    # Overrides conftest's stub_hooks for this module; `client` picks it up.
    return ToolScreen()


def _chat(client, topic_id: str) -> None:
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "hi", "summon": True})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass


def test_event_blocks_persist_structured_meta(client):
    p = client.post("/projects", json={"name": "P"}).json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "话题", "created_by": "user-1"},
    ).json()["data"]
    _chat(client, t["id"])

    tr = client.get(f"/topics/{t['id']}/transcript").json()["data"]["data"]
    by_tool = {b["meta"]["tool"]: b for b in tr if (b.get("meta") or {}).get("tool")}

    # Plain work → neutral dot; verb/arg live in meta for display-time labels.
    # in_room=False keeps 芝士's working detail out of the conversation (§14.1).
    grep = by_tool["Grep"]
    assert grep["meta"] == {
        "tool": "Grep",
        "arg": "TODO",
        "platform": False,
        "in_room": False,
    }
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
    assert fut["meta"] == {
        "tool": "FutureTool",
        "platform": False,
        "in_room": False,
    }


def test_transcript_pages_back_instead_of_serving_everything(client):
    """现场 is the biggest thing a topic can hand back — one event per tool call,
    forever. So it comes in windows, newest first, and the caller walks back."""
    p = client.post("/projects", json={"name": "P"}).json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "话题", "created_by": "user-1"},
    ).json()["data"]
    for _ in range(3):
        _chat(client, t["id"])

    everything = client.get(f"/topics/{t['id']}/transcript").json()["data"]["data"]
    assert len(everything) > 2, "需要几条事件才谈得上分页"

    first = client.get(f"/topics/{t['id']}/transcript?limit=2").json()["data"]
    assert len(first["data"]) == 2
    assert first["has_more"] is True
    # 最新的一窗：末尾必须和全量的末尾是同一条。
    assert first["data"][-1]["id"] == everything[-1]["id"]

    older = client.get(
        f"/topics/{t['id']}/transcript?limit=200&before={first['oldest_id']}"
    ).json()["data"]
    assert older["has_more"] is False
    # 两窗接起来就是全量，一条不多一条不少 —— 「过滤发生在分页之后」的实现会在
    # 这里露馅：它每一窗都少给几条，has_more 也算错。
    assert [b["id"] for b in older["data"] + first["data"]] == [
        b["id"] for b in everything
    ]


def test_transcript_rejects_a_cursor_from_another_topic(client):
    """未知游标不能悄悄退化成「最新 N 条」—— 调用方分不出那和真的一页有什么区别。"""
    p = client.post("/projects", json={"name": "P"}).json()["data"]
    a = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "A", "created_by": "user-1"},
    ).json()["data"]
    b = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "B", "created_by": "user-1"},
    ).json()["data"]
    _chat(client, a["id"])
    other = client.get(f"/topics/{a['id']}/transcript").json()["data"]["data"][0]["id"]

    assert (
        client.get(f"/topics/{b['id']}/transcript?limit=5&before={other}").status_code
        == 404
    )
