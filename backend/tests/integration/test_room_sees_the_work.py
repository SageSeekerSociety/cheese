"""房间看得见分身查了什么、这一轮改了什么 — end to end through a real turn.

Two facts used to be invisible in the room and are asserted here:

- a subagent's CONCLUSION (the call was already visible; the answer was not);
- the turn's own change summary («这一轮改了 N 个文件»), which no event covered —
  the timeline had every individual 改文件 line and no net result.
"""

import pytest

from app.domain.agent.service import (
    AgentResult,
    AgentToolResult,
    AgentToolUse,
    AgentUsage,
)
from app.domain.workspace import service as ws
from tests.conftest import StubAgent
from tests.integration.conftest import chat_ws_url

DIFF = """diff --git a/backend/app/x.py b/backend/app/x.py
--- a/backend/app/x.py
+++ b/backend/app/x.py
@@ -1,2 +1,3 @@
 keep
-gone
+added one
+added two
"""


class SubagentStubAgent(StubAgent):
    """Spawns a subagent, then hands its conclusion back — the two halves the
    room needs to pair up."""

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
        yield AgentToolUse(name="Task", input={"description": "查分页接口现状"})
        yield AgentToolResult(
            name="Task",
            description="查分页接口现状",
            text="结论：分页用的是 offset，\n改动点在路由层",
            eid="sub-1",
        )
        yield AgentResult(
            text="查完了",
            session_id="sess-sub-1",
            usage=AgentUsage(model="stub", input_tokens=1, output_tokens=1),
        )


@pytest.fixture
def stub_agent() -> SubagentStubAgent:
    # Overrides conftest's stub_agent for this module; `client` picks it up.
    return SubagentStubAgent()


def _chat(client, topic_id: str) -> None:
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws_conn:
        ws_conn.send_json({"type": "message", "content": "hi", "summon": True})
        while ws_conn.receive_json()["type"] not in ("done", "error"):
            pass


def _topic(client) -> str:
    p = client.post("/projects", json={"name": "P"}).json()["data"]
    return client.post(
        "/topics",
        json={"project_id": p["id"], "title": "话题", "created_by": "user-1"},
    ).json()["data"]["id"]


def _transcript(client, topic_id: str) -> list[dict]:
    """施工现场 — event blocks only (that endpoint filters kind=event)."""
    return client.get(f"/topics/{topic_id}/transcript").json()["data"]["data"]


def _blocks(client, topic_id: str) -> list[dict]:
    """Everything in the room, messages included."""
    return client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]


def test_a_subagents_conclusion_lands_in_the_room(client):
    topic_id = _topic(client)
    _chat(client, topic_id)
    blocks = _transcript(client, topic_id)

    # The call was already visible before this change…
    calls = [b for b in blocks if (b.get("meta") or {}).get("tool") == "Task"]
    assert [b["content"] for b in calls] == ["派分身去查\n查分页接口现状"]

    # …and now so is the answer.
    results = [b for b in blocks if (b.get("meta") or {}).get("subagent")]
    assert len(results) == 1
    result = results[0]
    assert result["kind"] == "event"
    assert result["content"] == (
        "分身查完了：查分页接口现状\n结论：分页用的是 offset， 改动点在路由层"
    )
    assert result["meta"]["subagent"] == {
        "tool": "Task",
        "description": "查分页接口现状",
        "summary": "结论：分页用的是 offset， 改动点在路由层",
        "truncated": False,
    }
    # It comes after the call it answers.
    assert blocks.index(result) > blocks.index(calls[0])


def test_the_turn_ends_with_a_change_summary(client, monkeypatch):
    """The commits that were NOT on the branch at turn start are this turn's."""
    log_calls: list[int] = []

    def fake_git_log(project_id, limit=50, topic_id=None):
        log_calls.append(limit)
        # First read is the turn-start baseline (nothing yet); the second is
        # after the checkpoint turned this turn's edits into a commit.
        if len(log_calls) == 1:
            return []
        return [{"hash": "abc1234", "author": "芝士", "message": "chore: snapshot"}]

    def fake_git_diff(project_id, ref=None):
        assert ref == "abc1234"
        return DIFF

    monkeypatch.setattr(ws, "git_log", fake_git_log)
    monkeypatch.setattr(ws, "git_diff", fake_git_diff)

    topic_id = _topic(client)
    _chat(client, topic_id)
    blocks = _transcript(client, topic_id)

    summaries = [b for b in blocks if (b.get("meta") or {}).get("changeset")]
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["kind"] == "event"
    assert summary["content"] == "这一轮改了 1 个文件（+2 -1）\nbackend/app/x.py"
    assert summary["meta"]["changeset"]["commit"] == "abc1234"
    assert summary["meta"]["changeset"]["files"] == [
        {"path": "backend/app/x.py", "added": 2, "removed": 1}
    ]


def test_a_turn_that_changed_nothing_says_nothing(client, monkeypatch):
    """No new commit → no summary. 不刷屏 also means not posting an empty one."""
    monkeypatch.setattr(
        ws,
        "git_log",
        lambda project_id, limit=50, topic_id=None: [
            {"hash": "old0000", "author": "芝士", "message": "chore: snapshot"}
        ],
    )

    def never(project_id, ref=None):
        raise AssertionError("no new commit — nothing to diff")

    monkeypatch.setattr(ws, "git_diff", never)

    topic_id = _topic(client)
    _chat(client, topic_id)
    blocks = _transcript(client, topic_id)
    assert not [b for b in blocks if (b.get("meta") or {}).get("changeset")]


def test_a_broken_workspace_never_fails_the_turn(client, monkeypatch):
    """The numbers are a courtesy. A git failure must cost the summary, not the
    turn — and must not leave a summary measured against no baseline."""

    def boom(*_args, **_kwargs):
        raise OSError("no workspace here")

    monkeypatch.setattr(ws, "git_log", boom)

    topic_id = _topic(client)
    _chat(client, topic_id)
    assert not [
        b
        for b in _transcript(client, topic_id)
        if (b.get("meta") or {}).get("changeset")
    ]
    # The turn itself still landed 芝士's reply.
    assert any(
        b["kind"] == "message" and b["author_type"] == "ai"
        for b in _blocks(client, topic_id)
    )
