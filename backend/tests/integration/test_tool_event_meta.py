"""Structured tool-event persistence: each 现场 event block stores {tool, arg,
platform} in `meta` so the UI translates and colors dots at DISPLAY time —
including tools missing from today's verb table, and subagent-nested calls
(which arrive through the same AgentToolUse path with the same field shapes)."""

import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import StubChannel
from tests.integration.conftest import (
    in_thread,
    post_message,
    post_project,
    room_socket,
)


class ToolScreen(StubChannel):
    """A session using a mix of platform / plain / unmapped tools."""

    def emit_turn(
        self,
        topic_id: uuid.UUID,
        prompt: str,
        reply: str,
        *,
        agent: str | None = None,
    ) -> None:
        del reply
        self.starts(topic_id)
        self.acknowledges(topic_id, prompt)
        self.uses(topic_id, "Grep", pattern="TODO", path="src")
        self.uses(topic_id, "mcp__cheese__update_doc", content="# 文档")
        self.uses(topic_id, "Bash", command="cheese sync --all")
        self.uses(topic_id, "Bash", command="ls -la")
        self.uses(topic_id, "FutureTool", x=1)
        self.stops(topic_id, "done")


@pytest.fixture
def stub_hooks() -> ToolScreen:
    # Overrides conftest's stub_hooks for this module; `client` picks it up.
    return ToolScreen()


def _display(meta: dict) -> dict:
    return {key: value for key, value in meta.items() if key != "eid"}


def _chat(client, topic_id: str) -> None:
    with room_socket(client, topic_id, "user-1") as ws:
        post_message(client, topic_id, "user-1", {"content": "@芝士 hi"})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass


def test_native_tools_are_no_longer_written_to_event_blocks(client):
    p = post_project(client, json={"name": "P"}, owner="user-1").json()["data"]
    t = client.post("/topics", json={"project_id": p["id"], "title": "Tools"}).json()[
        "data"
    ]
    topic = in_thread(client, t["id"], "user-1")
    _chat(client, topic)
    from sqlalchemy import select

    from app.domain.block.models import Block

    async def read():
        async with client.test_request_factory() as db:
            return list(
                await db.scalars(
                    select(Block).where(Block.conversation_id == uuid.UUID(topic))
                )
            )

    rows = client.portal.call(read)
    assert not [row for row in rows if (row.meta or {}).get("tool")]


def test_transcript_pages_back_instead_of_serving_everything(client):
    """现场 is the biggest thing a topic can hand back — one event per tool call,
    forever. So it comes in windows, newest first, and the caller walks back."""
    p = post_project(client, json={"name": "P"}, owner="user-1").json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "话题"},
    ).json()["data"]
    t = {"id": in_thread(client, t["id"], "user-1")}
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
    p = post_project(client, json={"name": "P"}, owner="user-1").json()["data"]
    a = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "A"},
    ).json()["data"]
    b = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "B"},
    ).json()["data"]
    a = {"id": in_thread(client, a["id"], "user-1")}
    b = {"id": in_thread(client, b["id"], "user-1")}
    _chat(client, a["id"])
    other = client.get(f"/topics/{a['id']}/transcript").json()["data"]["data"][0]["id"]

    assert (
        client.get(f"/topics/{b['id']}/transcript?limit=5&before={other}").status_code
        == 404
    )


def test_transcript_says_when_each_turn_started(client):
    """A turn's first step comes after its preparation and the model's first
    answer, so 现场 cannot count a turn from its steps alone."""
    p = post_project(client, json={"name": "P"}, owner="user-1").json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "话题"},
    ).json()["data"]
    t = {"id": in_thread(client, t["id"], "user-1")}
    asked = datetime.now(UTC)
    _chat(client, t["id"])

    page = client.get(f"/topics/{t['id']}/transcript?limit=50").json()["data"]
    steps = [b for b in page["data"] if b.get("turn_id")]
    assert steps, "需要这一轮留下的步骤"
    turn = steps[0]["turn_id"]
    started = datetime.fromisoformat(page["turn_starts"][turn])
    first_step = min(
        datetime.fromisoformat(b["created_at"]) for b in steps if b["turn_id"] == turn
    )
    assert asked <= started <= first_step
