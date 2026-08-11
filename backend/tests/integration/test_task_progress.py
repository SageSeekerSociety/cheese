"""进度层: the working checklist outlives the machine that produced it.

芝士 keeps its checklist with Claude Code's Task tools, and that list lived only
in the session file inside the topic's container — so a re-created container, a
reclaimed machine or an accept (which frees the container) took 上次做到哪 with
it, and the next turn re-did finished work. The checklist is now folded onto the
topic row as it changes, and handed back to the next turn in its prompt.
"""

import json
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.agent.chat import ChatService
from app.domain.agent.service import (
    AgentResult,
    AgentService,
    AgentSessionInfo,
    AgentToolUse,
)
from app.domain.project.services import ProjectService
from app.domain.topic.repositories import TopicRepository
from app.domain.topic.services import TopicService
from app.domain.workspace import service as ws


class ScriptedAgent(AgentService):
    """A turn that emits a fixed list of events, and records the system prompt it
    was handed (that prompt is how a new session learns what's already done)."""

    def __init__(self, events: list, session_id: str = "s1") -> None:
        super().__init__(model="stub")
        self._events = events
        self._session_id = session_id
        self.system_prompts: list[str] = []

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
        self.system_prompts.append(system_prompt)
        yield AgentSessionInfo(session_id=self._session_id)
        for event in self._events:
            yield event
        yield AgentResult(text="好了", session_id=self._session_id, usage=None)


def _svc(factory, agent: AgentService, root: Path) -> ChatService:
    return ChatService(
        session_factory=factory,
        agent=agent,
        base_system_prompt="你是芝士。",
        workspace_root=str(root),
    )


async def _topic(factory) -> tuple[uuid.UUID, uuid.UUID]:
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        ids = (project.id, topic.id)
        await session.commit()
    return ids


async def _progress(factory, topic_id: uuid.UUID) -> dict:
    async with factory() as session:
        topic = await TopicRepository(session).get(topic_id)
        return topic.progress or {}


async def _run(svc: ChatService, topic_id: uuid.UUID, text: str = "做点事") -> None:
    async for _ in svc.converse(
        topic_id=topic_id, author="u", content=text, summon=True
    ):
        pass


def _task_hook(name: str, tool_input: dict) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": name,
        "tool_input": tool_input,
    }


def _spool(spool: Path, eid: str, payload: dict) -> None:
    """Simulate the cheese-hook forwarder's atomic write of one hook."""
    spool.mkdir(parents=True, exist_ok=True)
    (spool / f"1700000000000000000.{eid}").write_text(
        json.dumps(payload), encoding="utf-8"
    )


@pytest.mark.anyio
async def test_checklist_is_kept_on_the_topic_not_only_in_the_session(client, tmp_path):
    """What the agent ticked off is on the row after the turn — the machine is no
    longer the only place it exists."""
    factory = client.test_factory
    _, topic_id = await _topic(factory)
    agent = ScriptedAgent(
        [
            AgentToolUse(name="TaskCreate", input={"subject": "修登录"}, eid="e1"),
            AgentToolUse(name="TaskCreate", input={"subject": "加测试"}, eid="e2"),
            AgentToolUse(
                name="TaskUpdate",
                input={"taskId": "1", "status": "completed"},
                eid="e3",
            ),
        ]
    )
    await _run(_svc(factory, agent, tmp_path / "ws"), topic_id)

    saved = await _progress(factory, topic_id)
    assert [(i["subject"], i["status"]) for i in saved["items"]] == [
        ("修登录", "completed"),
        ("加测试", "pending"),
    ]
    assert saved["session_id"] == "s1"


@pytest.mark.anyio
async def test_a_resumed_session_keeps_ticking_the_same_list(client, tmp_path):
    """Same session resumed → the second turn continues the first turn's list
    (Claude's task ids carry over with the session), it does not start a new one."""
    factory = client.test_factory
    _, topic_id = await _topic(factory)
    first = ScriptedAgent(
        [AgentToolUse(name="TaskCreate", input={"subject": "修登录"}, eid="e1")]
    )
    await _run(_svc(factory, first, tmp_path / "ws"), topic_id)

    second = ScriptedAgent(
        [
            AgentToolUse(
                name="TaskUpdate",
                input={"taskId": "1", "status": "completed"},
                eid="e4",
            ),
            AgentToolUse(name="TaskCreate", input={"subject": "写文档"}, eid="e5"),
        ]
    )
    await _run(_svc(factory, second, tmp_path / "ws"), topic_id, "接着做")

    saved = await _progress(factory, topic_id)
    assert [(i["subject"], i["status"]) for i in saved["items"]] == [
        ("修登录", "completed"),
        ("写文档", "pending"),
    ]


@pytest.mark.anyio
async def test_next_turn_is_told_what_was_already_done(client, tmp_path):
    """The half that survives a lost machine: the session file is gone, so Claude
    starts blank — but the turn opens holding the finished/unfinished list."""
    factory = client.test_factory
    _, topic_id = await _topic(factory)
    first = ScriptedAgent(
        [
            AgentToolUse(name="TaskCreate", input={"subject": "修登录"}, eid="e1"),
            AgentToolUse(name="TaskCreate", input={"subject": "加测试"}, eid="e2"),
            AgentToolUse(
                name="TaskUpdate",
                input={"taskId": "1", "status": "completed"},
                eid="e3",
            ),
        ]
    )
    await _run(_svc(factory, first, tmp_path / "ws"), topic_id)

    # 换机器: the resume misses, Claude announces a session nobody asked for.
    second = ScriptedAgent([], session_id="s2-fresh")
    await _run(_svc(factory, second, tmp_path / "ws"), topic_id, "接着做")

    prompt = second.system_prompts[0]
    assert "✅ 修登录" in prompt
    assert "⬜ 加测试" in prompt


@pytest.mark.anyio
async def test_a_fresh_session_numbers_from_scratch(client, tmp_path):
    """The old ids die with the old session, so a fresh session's TaskCreate must
    not be filed under the previous list's numbering (a later TaskUpdate #1 would
    then tick the wrong line)."""
    factory = client.test_factory
    _, topic_id = await _topic(factory)
    first = ScriptedAgent(
        [AgentToolUse(name="TaskCreate", input={"subject": "旧任务"}, eid="e1")]
    )
    await _run(_svc(factory, first, tmp_path / "ws"), topic_id)

    second = ScriptedAgent(
        [AgentToolUse(name="TaskCreate", input={"subject": "新任务"}, eid="e9")],
        session_id="s2-fresh",
    )
    await _run(_svc(factory, second, tmp_path / "ws"), topic_id, "接着做")

    saved = await _progress(factory, topic_id)
    assert [(i["id"], i["subject"]) for i in saved["items"]] == [("1", "新任务")]
    assert saved["session_id"] == "s2-fresh"


@pytest.mark.anyio
async def test_a_task_event_that_only_reached_the_spool_is_not_lost(
    client, tmp_path, monkeypatch
):
    """The crash case this exists for: the turn died, its last checkbox only made
    it to the durable spool. Backfill has to fold it, not drop it as 'process'."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    project_id, topic_id = await _topic(factory)
    _spool(
        ws.spool_dir(project_id, topic_id),
        "spooled-1",
        _task_hook("TaskCreate", {"subject": "宕机前建的任务"}),
    )

    agent = ScriptedAgent([])
    await _run(_svc(factory, agent, tmp_path / "ws"), topic_id)

    saved = await _progress(factory, topic_id)
    assert [i["subject"] for i in saved["items"]] == ["宕机前建的任务"]


@pytest.mark.anyio
async def test_the_same_event_delivered_twice_adds_one_task(
    client, tmp_path, monkeypatch
):
    """A hook reaches the backend live AND sits in the spool. Replaying it must
    not duplicate the task — the row remembers which event-ids it folded."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    project_id, topic_id = await _topic(factory)
    agent = ScriptedAgent(
        [AgentToolUse(name="TaskCreate", input={"subject": "只该有一条"}, eid="dup-1")]
    )
    await _run(_svc(factory, agent, tmp_path / "ws"), topic_id)

    # The same delivery, now replayed from the spool on the next turn.
    _spool(
        ws.spool_dir(project_id, topic_id),
        "dup-1",
        _task_hook("TaskCreate", {"subject": "只该有一条"}),
    )
    await _run(_svc(factory, ScriptedAgent([]), tmp_path / "ws"), topic_id, "再来")

    saved = await _progress(factory, topic_id)
    assert [i["subject"] for i in saved["items"]] == ["只该有一条"]
