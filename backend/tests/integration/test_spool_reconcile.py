"""Durable spool reconcile: a 现场 tool event whose live hook delivery was lost
(backend down / no listener during a prior turn) is backfilled from the on-disk
WAL at the next turn start — idempotently (no duplicate if it was also persisted
live). This is the W1 half of the event-durability fix."""

import json
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.agent.chat import ChatService
from app.domain.agent.service import AgentResult, AgentService, AgentToolUse
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from app.domain.workspace import service as ws


class QuietAgent(AgentService):
    """A turn that produces no events of its own — so the only 现场 event under
    test is the one recovered from the spool."""

    def __init__(self) -> None:
        super().__init__(model="stub")

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
        yield AgentResult(text="ok", session_id="s1", usage=None)


_PRE_TOOL_USE = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "echo hi"},
}


def _spool_event(spool: Path, eid: str, payload: dict | None = None) -> None:
    """Simulate the cheese-hook forwarder's atomic write of one hook."""
    spool.mkdir(parents=True, exist_ok=True)
    (spool / f"1700000000000000000.{eid}").write_text(
        json.dumps(payload or _PRE_TOOL_USE), encoding="utf-8"
    )


def _event_blocks_for(rows, eid: str):
    return [
        b
        for b in rows
        if b.kind == BlockKind.event
        and isinstance(b.meta, dict)
        and b.meta.get("eid") == eid
    ]


@pytest.mark.anyio
async def test_spooled_event_is_backfilled_then_deduped(client, tmp_path, monkeypatch):
    # The backend reader (ws.spool_dir) and the container writer both key off
    # settings.workspace_root, so point it at the test's tmp dir.
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    svc = ChatService(
        session_factory=factory,
        agent=QuietAgent(),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        project_id: uuid.UUID = project.id
        topic_id: uuid.UUID = topic.id
        await session.commit()

    # A 现场 event whose live hook POST was lost while the backend was down — it
    # only reached the durable spool.
    eid = "evt-1"
    _spool_event(ws.spool_dir(project_id, topic_id), eid)

    # A turn runs → reconcile at its start backfills the spooled event.
    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="做点事", summon=True
    ):
        pass

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    backfilled = _event_blocks_for(rows, eid)
    assert len(backfilled) == 1
    assert backfilled[0].meta.get("backfilled") is True
    assert "echo hi" in backfilled[0].content
    # The spool was drained (files removed after reconcile).
    assert not list(ws.spool_dir(project_id, topic_id).iterdir())

    # Even if the same event reappears in the spool, a later turn must NOT
    # duplicate it — dedup is by event-id against already-persisted blocks.
    _spool_event(ws.spool_dir(project_id, topic_id), eid)
    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="再来", summon=True
    ):
        pass
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    assert len(_event_blocks_for(rows, eid)) == 1  # deduped, not duplicated


@pytest.mark.anyio
async def test_spooled_chat_message_is_backfilled(client, tmp_path, monkeypatch):
    """A 芝士 chat message whose live delivery was lost lands as history on the
    next turn — as a message block (backfilled), deduped by eid."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    svc = ChatService(
        session_factory=factory,
        agent=QuietAgent(),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        pid, tid = project.id, topic.id
        await session.commit()

    _spool_event(
        ws.spool_dir(pid, tid),
        "msg-1",
        {"hook_event_name": "MessageDisplay", "delta": "宕机期间说的话"},
    )
    async for _ in svc.converse(topic_id=tid, author="u", content="继续", summon=True):
        pass
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(tid)
    backfilled = [
        b
        for b in rows
        if b.kind == BlockKind.message
        and isinstance(b.meta, dict)
        and b.meta.get("eid") == "msg-1"
    ]
    assert len(backfilled) == 1
    assert backfilled[0].content == "宕机期间说的话"
    assert backfilled[0].meta.get("backfilled") is True

    # Same eid re-spooled → not duplicated (dedup spans message blocks too).
    _spool_event(
        ws.spool_dir(pid, tid),
        "msg-1",
        {"hook_event_name": "MessageDisplay", "delta": "宕机期间说的话"},
    )
    async for _ in svc.converse(topic_id=tid, author="u", content="再来", summon=True):
        pass
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(tid)
    assert (
        len(
            [
                b
                for b in rows
                if isinstance(b.meta, dict) and b.meta.get("eid") == "msg-1"
            ]
        )
        == 1
    )


class _DupToolAgent(AgentService):
    """Emits the SAME tool event twice (same event-id) — a device drainer
    re-delivery after a lost ack — then finishes."""

    def __init__(self) -> None:
        super().__init__(model="stub")

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
        tool = AgentToolUse(name="Bash", input={"command": "echo hi"}, eid="dup-1")
        yield tool
        yield tool
        yield AgentResult(text="ok", session_id="s1", usage=None)


@pytest.mark.anyio
async def test_duplicate_tool_event_is_deduped_by_event_id(client, tmp_path):
    factory = client.test_factory
    svc = ChatService(
        session_factory=factory,
        agent=_DupToolAgent(),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id = topic.id
        await session.commit()

    frames = [
        f
        async for f in svc.converse(
            topic_id=topic_id, author="u", content="做点事", summon=True
        )
    ]
    # The re-delivery is dropped before it is streamed or persisted.
    assert sum(1 for f in frames if f["type"] == "tool") == 1
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    assert len(_event_blocks_for(rows, "dup-1")) == 1
