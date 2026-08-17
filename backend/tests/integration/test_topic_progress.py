"""进度层 (#187): 芝士's checklist outlives the turn, the machine and the session.

Before this, TaskCreate/TaskUpdate only ever existed as WS frames — the room saw
a live checklist while a turn ran and nothing at all afterwards. So a topic
picked up on a new machine (or after a crash) had no way to answer "做到哪了",
which is the failure #187 is about. These tests pin the three properties that
make the layer real: it survives the turn, it survives a turn that DIES, and the
next turn is actually told about it.
"""

import pytest

from app.domain.agent.service import (
    AgentResult,
    AgentToolUse,
    AgentUsage,
)
from tests.conftest import StubAgent
from tests.integration.conftest import chat_ws_url


class ChecklistAgent(StubAgent):
    """Builds a 3-item checklist, finishes one, starts the next, then returns."""

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
        yield AgentToolUse(name="TaskCreate", input={"subject": "核实 issue 论断"})
        yield AgentToolUse(name="TaskCreate", input={"subject": "写实现"})
        yield AgentToolUse(name="TaskCreate", input={"subject": "补测试"})
        yield AgentToolUse(
            name="TaskUpdate", input={"taskId": "1", "status": "completed"}
        )
        yield AgentToolUse(
            name="TaskUpdate", input={"taskId": "2", "status": "in_progress"}
        )
        yield AgentResult(
            text="干到一半",
            session_id="sess-progress-1",
            usage=AgentUsage(model="stub", input_tokens=1, output_tokens=1),
        )


class DyingChecklistAgent(StubAgent):
    """Records progress, then the turn dies — the machine-death case."""

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
        yield AgentToolUse(name="TaskCreate", input={"subject": "跑到一半就没了"})
        yield AgentToolUse(
            name="TaskUpdate", input={"taskId": "1", "status": "in_progress"}
        )
        raise RuntimeError("host disappeared")


@pytest.fixture
def stub_agent() -> ChecklistAgent:
    # Overrides conftest's stub_agent for this module; `client` picks it up.
    return ChecklistAgent()


def _topic(client) -> str:
    p = client.post("/projects", json={"name": "P", "owner_handle": "user-1"}).json()[
        "data"
    ]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "话题", "created_by": "user-1"},
    ).json()["data"]
    return t["id"]


def _chat(client, topic_id: str) -> list[dict]:
    """Run one turn, returning every frame it produced."""
    frames: list[dict] = []
    # The chat socket requires a session token; take the same path the browser
    # does via the shared helper (see .claude/rules/backend-tests.md).
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": "hi", "summon": True})
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] in ("done", "error"):
                return frames


def test_topic_without_a_turn_has_empty_progress(client):
    body = client.get(f"/topics/{_topic(client)}/progress").json()
    assert body["code"] == 200
    assert body["data"] == {"items": [], "updated_at": None}


def test_checklist_outlives_the_turn(client):
    tid = _topic(client)
    _chat(client, tid)

    data = client.get(f"/topics/{tid}/progress").json()["data"]
    assert [(i["subject"], i["status"]) for i in data["items"]] == [
        ("核实 issue 论断", "completed"),
        ("写实现", "in_progress"),
        ("补测试", "pending"),
    ]
    # Stamped, so a reader can tell fresh progress from something ancient.
    assert data["updated_at"] is not None


def test_next_turn_is_told_where_the_work_got_to(client, stub_agent):
    tid = _topic(client)
    _chat(client, tid)

    first_turn_prompt = stub_agent.last_system_prompt or ""
    assert "上次的任务清单" not in first_turn_prompt  # nothing to carry yet

    frames = _chat(client, tid)

    prompt = stub_agent.last_system_prompt or ""
    assert "上次的任务清单" in prompt
    # Status is carried as a mark, not just the text: "已完成" vs "在做" is the
    # whole reason to hand the list over rather than re-plan from scratch.
    assert "- [x] 核实 issue 论断" in prompt
    assert "- [~] 写实现" in prompt
    assert "- [ ] 补测试" in prompt
    # And the agent is told to re-list finished items — the stored row is
    # overwritten by this turn's first TaskCreate, so a plan that dropped them
    # would erase the progress it was just handed.
    assert "completed" in prompt

    # The room sees it too, before the turn produces anything of its own.
    restored = [f for f in frames if f["type"] == "todo" and f.get("restored")]
    assert restored, "turn start must replay the stored checklist to the room"
    assert [i["subject"] for i in restored[0]["items"]] == [
        "核实 issue 论断",
        "写实现",
        "补测试",
    ]


def test_progress_survives_a_turn_that_dies(client, stub_agent, monkeypatch):
    """The case the whole layer exists for: the turn does NOT get to finish.

    Progress is written the moment each Task tool streams in, not batched to
    turn end — batching would lose exactly this."""
    tid = _topic(client)
    monkeypatch.setattr(stub_agent, "stream_reply", DyingChecklistAgent().stream_reply)

    _chat(client, tid)  # ends in an error frame, not done

    data = client.get(f"/topics/{tid}/progress").json()["data"]
    assert [(i["subject"], i["status"]) for i in data["items"]] == [
        ("跑到一半就没了", "in_progress")
    ]


def test_progress_is_per_topic(client):
    a, b = _topic(client), _topic(client)
    _chat(client, a)

    assert client.get(f"/topics/{b}/progress").json()["data"]["items"] == []


def test_progress_404s_for_an_unknown_topic(client):
    unknown = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/topics/{unknown}/progress").status_code == 404
