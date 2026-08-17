"""Attach 收账 (#316 root-cause side): a backend restart kills the waiter, not
the working claude. The orphan sweep no longer re-prompts a turn claude already
received — so the spool settle has to finish the turn instead: a Stop parked
with no listener lands the final message, saves the finished session pointer,
and an undelivered prompt (the ONE re-send case) goes out as the original text.
"""

import asyncio
import json
import time as _time
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import runtime as rt
from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker
from app.domain.agent.service import AgentResult, AgentService
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.topic.repositories import TopicRepository
from app.domain.topic.services import TopicService
from app.domain.workspace import service as ws


def _spool_event(spool: Path, eid: str, payload: dict) -> None:
    """Simulate the cheese-hook forwarder's atomic write of one hook."""
    spool.mkdir(parents=True, exist_ok=True)
    (spool / f"{_time.time_ns()}.{eid}").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


class _MustNotRun(AgentService):
    """An agent whose invocation IS the failure: attach mode means no prompt."""

    def __init__(self) -> None:
        super().__init__(model="stub")

    async def stream_reply(self, **_):
        raise AssertionError("attach mode must never start a turn")
        yield  # pragma: no cover — makes this an async generator


class _RecordingAgent(AgentService):
    """Records every prompt it is asked to run (the re-send path's witness)."""

    def __init__(self) -> None:
        super().__init__(model="stub")
        self.prompts: list[str] = []

    async def stream_reply(self, *, prompt, **_):
        self.prompts.append(prompt)
        yield AgentResult(text="收到", session_id="s-new", usage=None)


async def _seed_topic(factory) -> tuple[uuid.UUID, uuid.UUID]:
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        pid, tid = project.id, topic.id
        await session.commit()
    return pid, tid


@pytest.mark.anyio
async def test_settle_lands_parked_stop_and_finishes_the_turn(
    client, tmp_path, monkeypatch
):
    """MessageDisplay + Stop parked while nobody listened: the settle lands the
    message once (the Stop's copy of the same text is deduped), saves the
    finished session pointer, and empties the spool."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    pid, tid = await _seed_topic(factory)
    svc = ChatService(
        session_factory=factory,
        agent=_MustNotRun(),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )

    _spool_event(
        ws.spool_dir(pid, tid),
        "m1",
        {"hook_event_name": "MessageDisplay", "delta": "收尾汇报：都做完了"},
    )
    _spool_event(
        ws.spool_dir(pid, tid),
        "s1",
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "收尾汇报：都做完了",
            "session_id": "s-done",
        },
    )

    landed = await svc.settle_spool(tid)
    assert landed == 1  # the message once — Stop's twin text deduped

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(tid)
        topic = await TopicRepository(session).get(tid)
    finals = [
        b
        for b in rows
        if b.kind == BlockKind.message and b.content == "收尾汇报：都做完了"
    ]
    assert len(finals) == 1
    assert finals[0].meta.get("backfilled") is True
    assert topic.session_id == "s-done"  # the next summon resumes the FINISHED session
    assert not list(ws.spool_dir(pid, tid).iterdir())


@pytest.mark.anyio
async def test_settle_lands_a_stop_only_final_message(client, tmp_path, monkeypatch):
    """A Stop whose MessageDisplay never made it anywhere still lands its
    last_assistant_message — the turn's ending must not be lost with it."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    pid, tid = await _seed_topic(factory)
    svc = ChatService(
        session_factory=factory,
        agent=_MustNotRun(),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    _spool_event(
        ws.spool_dir(pid, tid),
        "s-only",
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "只有Stop带回来的结论",
            "session_id": "s-final",
        },
    )

    assert await svc.settle_spool(tid) == 1
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(tid)
        topic = await TopicRepository(session).get(tid)
    finals = [b for b in rows if b.content == "只有Stop带回来的结论"]
    assert len(finals) == 1
    assert finals[0].kind == BlockKind.message
    assert finals[0].meta.get("eid") == "s-only"
    assert topic.session_id == "s-final"
    # Idempotent: a second settle finds nothing to do.
    assert await svc.settle_spool(tid) == 0


@pytest.mark.anyio
async def test_orphan_with_parked_stop_is_settled_not_reprompted(
    client, tmp_path, monkeypatch
):
    """The full chain of the incident fix: orphan turn + a Stop in the spool →
    the sweep attaches (no prompt reaches the agent), and the settle it
    schedules finishes the turn on its own."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    factory = client.test_factory
    pid, tid = await _seed_topic(factory)
    svc = ChatService(
        session_factory=factory,
        agent=_MustNotRun(),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    # No test should wait out the production debounce.
    real_schedule = ChatService.schedule_spool_settle
    monkeypatch.setattr(
        ChatService,
        "schedule_spool_settle",
        lambda self, topic_id, delay_s=2.0: real_schedule(self, topic_id, delay_s=0),
    )

    rt._save_inflight(
        {
            str(uuid.uuid4()): {
                "topic_id": str(tid),
                "started_at": _time.time() - 300,
                "is_resume": False,
                "author": "u",
                "content": "把测试跑绿",
            }
        }
    )
    _spool_event(
        ws.spool_dir(pid, tid),
        "s1",
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "跑绿了，收工",
            "session_id": "s-done",
        },
    )

    runner = AgentWorkRunner(InProcessBroker())
    assert await runner.resume_orphans(svc) == 0  # attach — nothing re-prompted

    async def _final_landed() -> bool:
        async with factory() as session:
            rows = await BlockRepository(session).list_for_topic(tid)
        return any(b.content == "跑绿了，收工" for b in rows)

    for _ in range(200):
        if await _final_landed():
            break
        await asyncio.sleep(0.01)
    assert await _final_landed()

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(tid)
    verdicts = [
        b
        for b in rows
        if b.author_type == AuthorType.system and "部署中断" in (b.content or "")
    ]
    assert len(verdicts) == 1  # the room was told, honestly and once
    assert rt._load_inflight() == {}


@pytest.mark.anyio
async def test_zero_evidence_orphan_resends_the_original_text(
    client, tmp_path, monkeypatch
):
    """No block, no spool trace → the sweep re-sends, and the turn's prompt is
    the pending HUMAN message verbatim — not a continuation nudge."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    factory = client.test_factory
    _pid, tid = await _seed_topic(factory)
    agent = _RecordingAgent()
    svc = ChatService(
        session_factory=factory,
        agent=agent,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    # The interrupted turn had posted its human message before dying — that is
    # what the pending-message mechanism re-hands to the re-sent turn.
    async with factory() as session:
        await BlockRepository(session).add(
            project_id=_pid,
            topic_id=tid,
            author="u",
            author_type=AuthorType.human,
            content="修一下登录页",
            kind=BlockKind.message,
        )
        await session.commit()
    rt._save_inflight(
        {
            str(uuid.uuid4()): {
                "topic_id": str(tid),
                "started_at": _time.time() - 300,
                "is_resume": False,
                "author": "u",
                "content": "修一下登录页",
            }
        }
    )
    # Collapse the 3s re-send delay, keep the real path.
    real_resend = AgentWorkRunner._schedule_resend
    monkeypatch.setattr(
        AgentWorkRunner,
        "_schedule_resend",
        lambda self, chat, topic_id, after_s, content, **kw: real_resend(
            self, chat, topic_id, 0.0, content, **kw
        ),
    )

    runner = AgentWorkRunner(InProcessBroker())
    assert await runner.resume_orphans(svc) == 1
    for _ in range(300):
        if agent.prompts:
            break
        await asyncio.sleep(0.01)
    assert len(agent.prompts) == 1
    assert "[u]: 修一下登录页" in agent.prompts[0]  # the original text, verbatim


def test_parked_hook_schedules_a_settle(client, tmp_path, monkeypatch):
    """The endpoint half of attach 收账: parking an event with no listener must
    also schedule the settle that drains it — otherwise a no-longer-re-prompted
    orphan's events sit invisible until a human happens to speak."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    scheduled: list[uuid.UUID] = []
    monkeypatch.setattr(
        ChatService,
        "schedule_spool_settle",
        lambda self, topic_id, delay_s=2.0: scheduled.append(topic_id),
    )
    pid, tid = uuid.uuid4(), uuid.uuid4()
    token = mint_scoped_token(project_id=str(pid), topic_id=str(tid), ttl_s=60)

    r = client.post(
        f"/sandbox/hooks/{tid}",
        json={"hook_event_name": "Stop", "last_assistant_message": "收工"},
        headers={"X-Cheese-Token": token, "X-Cheese-Event-Id": "e1"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["delivered"] is False
    assert scheduled == [tid]
    # The event really is parked for that settle to find.
    assert len(list(ws.spool_dir(pid, tid).iterdir())) == 1
