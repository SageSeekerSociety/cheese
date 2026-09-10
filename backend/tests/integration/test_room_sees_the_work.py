"""房间看得见分身查了什么、这一轮改了什么 — end to end through a real turn.

Two facts used to be invisible in the room and are asserted here:

- a subagent's CONCLUSION (the call was already visible; the answer was not);
- the turn's own change summary («这一轮改了 N 个文件»), which no event covered —
  the timeline had every individual 改文件 line and no net result.
"""

import threading
import uuid

import pytest

from app.domain.workspace import service as ws
from tests.conftest import StubChannel
from tests.delivery import delivery_task
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


class SubagentScreen(StubChannel):
    """Spawns a subagent, then hands its conclusion back — the two halves the
    room needs to pair up."""

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        del reply
        self.starts(topic_id)
        self.acknowledges(topic_id, prompt)
        self.uses(topic_id, "Task", description="查分页接口现状")
        self.returns(
            topic_id,
            "Task",
            "结论：分页用的是 offset，\n改动点在路由层",
            eid="sub-1",
            description="查分页接口现状",
        )
        self.stops(topic_id, "查完了")


@pytest.fixture
def stub_hooks() -> SubagentScreen:
    # Overrides conftest's stub_hooks for this module; `client` picks it up.
    return SubagentScreen()


def _chat(client, topic_id: str) -> None:
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws_conn:
        ws_conn.send_json({"type": "message", "content": "hi", "summon": True})
        while ws_conn.receive_json()["type"] not in ("done", "error"):
            pass


def _topic(client) -> str:
    p = client.post("/projects", json={"name": "P"}).json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "话题", "created_by": "user-1"},
    ).json()["data"]["id"]
    delivery_task(client, room, commit=False)
    return room


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


def test_what_shows_in_the_room_is_its_own_field(client, monkeypatch):
    """露不露面写在 `meta.in_room` 里，不再靠 `author_type` 兼职。

    同一轮里两种事件都产生了：工具调用和分身结论是芝士干活的过程（只进现场），
    改动摘要是平台数出来的结果（进房间）。两件事以前挤在 `author_type` 一格里，
    于是「芝士写的、又该让人看见」根本表达不出来，而选错了没有任何报错——事件安
    静地永远不出现。
    """

    log_calls: list[int] = []

    def fake_git_log(project_id, limit=50, topic_id=None):
        log_calls.append(limit)
        if len(log_calls) == 1:
            return []  # turn-start baseline: nothing on the branch yet
        return [{"hash": "abc1234", "author": "芝士", "message": "chore: snapshot"}]

    monkeypatch.setattr(ws, "git_log", fake_git_log)
    monkeypatch.setattr(ws, "git_diff", lambda project_id, ref=None: DIFF)

    topic_id = _topic(client)
    _chat(client, topic_id)
    events = _transcript(client, topic_id)

    def in_room(block: dict) -> bool:
        return (block.get("meta") or {}).get("in_room") is not False

    summary = next(b for b in events if (b.get("meta") or {}).get("changeset"))
    assert in_room(summary)

    work = [b for b in events if (b.get("meta") or {}).get("tool")]
    conclusions = [b for b in events if (b.get("meta") or {}).get("subagent")]
    assert work and conclusions
    assert not [b for b in work + conclusions if in_room(b)]

    # …并且是那一格说了算：现场里藏起来的事件，作者写的是真作者。
    assert {b["author_type"] for b in conclusions} == {"ai"}


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


def test_the_baseline_read_never_delays_the_turns_start(
    client, monkeypatch, stub_hooks
):
    """The baseline is read through `git_log`, which ensures the repo exists —
    on a cold project that is a `git init` plus a base commit. Awaited in front
    of the provider it is charged to the start of EVERY turn, and a turn
    cancelled inside that window dies before it can store its session id
    (test_turn_exit_paths.py owns that contract). So the read must be started,
    not awaited, there: what it measures only becomes commits at the checkpoint.
    """
    agent_started = threading.Event()
    saw_the_agent_first: list[bool] = []

    def slow_git_log(*_args, **_kwargs):
        # Runs on a worker thread. If the turn awaits it before starting the
        # provider, the agent can never fire this event and the wait times out —
        # which is the regression, recorded rather than raised because
        # _known_commits swallows exceptions by design.
        saw_the_agent_first.append(agent_started.wait(timeout=5))
        return []

    stub_hooks.on_start = agent_started.set
    monkeypatch.setattr(ws, "git_log", slow_git_log)

    topic_id = _topic(client)
    _chat(client, topic_id)

    assert saw_the_agent_first and all(saw_the_agent_first), "基线读取挡住了轮次启动"


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
    # The turn still records terminal output even when the workspace is broken.
    assert any(
        b["kind"] == "event"
        and b["author_type"] == "ai"
        and (b.get("meta") or {}).get("progress")
        for b in _blocks(client, topic_id)
    )
