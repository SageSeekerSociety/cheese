"""Durable spool reconcile: a 现场 tool event whose live hook delivery was lost
(backend down / no listener during a prior turn) is backfilled from the on-disk
WAL at the next turn start — idempotently (no duplicate if it was also persisted
live). This is the W1 half of the event-durability fix."""

import json
import os
import time
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.agent.chat import _SPOOL_PARTIAL_GRACE_S, ChatService
from app.domain.agent.harness.claude_code import event_spool
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.project.models import ProjectMember
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from app.domain.workspace import service as ws
from tests.conftest import StubChannel, settle_turn, stub_compute


class QuietScreen(StubChannel):
    """A turn that produces no events of its own — so the only 现场 event under
    test is the one recovered from the spool."""

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        del reply
        self.acknowledges(topic_id, prompt)
        self.stops(topic_id, "ok", session_id="s1")


_PRE_TOOL_USE = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "echo hi"},
}


def _spool_event(spool: Path, eid: str, payload: dict | None = None) -> None:
    """Simulate the cheese-hook forwarder's atomic write of one hook."""
    event_spool.append(spool, eid, payload or _PRE_TOOL_USE)


def _unread(spool: Path) -> list[str]:
    """Event ids the spool has NOT handed to the timeline yet.

    Reading no longer deletes: the file stays for its retention window and a
    cursor records how far a reader got. So "drained" is an empty tail, not an
    empty directory — asserting on the directory would be asserting on the
    disposal schedule.
    """
    return [
        eid
        for _path, eid, _payload in event_spool.spool_entries(
            spool, after=event_spool.read_cursor(spool)
        )
    ]


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
        compute=stub_compute(QuietScreen()),
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
    await settle_turn(svc, topic_id)

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    backfilled = _event_blocks_for(rows, eid)
    assert len(backfilled) == 1
    assert backfilled[0].meta.get("backfilled") is True
    assert "echo hi" in backfilled[0].content
    assert _unread(ws.spool_dir(project_id, topic_id)) == []

    # Even if the same event reappears in the spool, a later turn must NOT
    # duplicate it — dedup is by event-id against already-persisted blocks.
    _spool_event(ws.spool_dir(project_id, topic_id), eid)
    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="再来", summon=True
    ):
        pass
    await settle_turn(svc, topic_id)
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    assert len(_event_blocks_for(rows, eid)) == 1  # deduped, not duplicated
    # And the copy it skipped is READ, not left waiting: every hook is written
    # to this spool, so a pass that only moved its cursor over what it landed
    # would re-scan the whole history of the topic at every turn.
    assert _unread(ws.spool_dir(project_id, topic_id)) == []


@pytest.mark.anyio
async def test_spooled_chat_message_is_backfilled(client, tmp_path, monkeypatch):
    """A 芝士 chat message whose live delivery was lost lands as history on the
    next turn — as a message block (backfilled), deduped by eid."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(QuietScreen()),
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
    await settle_turn(svc, tid)
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
    await settle_turn(svc, tid)
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


class _DupToolScreen(StubChannel):
    """Emits the SAME tool event twice (same event-id) — a device drainer
    re-delivery after a lost ack — then finishes."""

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        del reply
        self.acknowledges(topic_id, prompt)
        for _ in range(2):
            self.uses(topic_id, "Bash", eid="dup-1", command="echo hi")
        self.stops(topic_id, "ok", session_id="s1")


@pytest.mark.anyio
async def test_backfilled_events_are_broadcast_not_just_persisted(
    client, tmp_path, monkeypatch
):
    """A spooled event/message the live hook path missed must reach the
    frontend when the next turn backfills it — not just land silently in the
    DB (bug: the WS frame stream never carried it, so a turn's own author saw
    nothing while the DB quietly gained a row nobody's client displayed)."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(QuietScreen()),
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

    _spool_event(ws.spool_dir(pid, tid), "evt-bcast")
    _spool_event(
        ws.spool_dir(pid, tid),
        "msg-bcast",
        {"hook_event_name": "MessageDisplay", "delta": "宕机期间说的话"},
    )
    frames = [
        f
        async for f in svc.converse(
            topic_id=tid, author="u", content="继续", summon=True
        )
    ]

    event_frames = [
        f
        for f in frames
        if f["type"] == "event_block" and "echo hi" in f["block"]["content"]
    ]
    assert len(event_frames) == 1

    message_frames = [
        f
        for f in frames
        if f["type"] == "assistant_block" and f["block"]["content"] == "宕机期间说的话"
    ]
    assert len(message_frames) == 1


class _LateSpoolScreen(StubChannel):
    """A hooks turn whose final message's OWN MessageDisplay hook lost the race
    with its Stop hook: the hook lands in the spool WHILE this turn is still
    running (mirrors the container's synchronous pre-curl spool write racing
    the live POST), so assistant_count stays 0 and the turn's result text
    exactly echoes what that dropped hook would have delivered."""

    def __init__(self, spool: Path, text: str) -> None:
        super().__init__()
        self._spool = spool
        self._text = text
        self.turns = 0

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        del reply
        self.acknowledges(topic_id, prompt)
        self.turns += 1
        if self.turns > 1:
            # A later turn: its reconcile is what has to recognize the spooled
            # copy as the message this session already delivered.
            self.stops(topic_id, "接着干", session_id="s1")
            return
        _spool_event(
            self._spool,
            "late-msg-1",
            {"hook_event_name": "MessageDisplay", "delta": self._text},
        )
        self.stops(topic_id, self._text, session_id="s1")


@pytest.mark.anyio
async def test_fallback_reply_does_not_duplicate_a_late_spooled_message(
    client, tmp_path, monkeypatch
):
    """The fallback path (no discrete AgentMessage this turn) must not leave a
    same-text duplicate once the spool catches up: an eid-less fallback block
    plus a LATER eid+backfilled twin that reconcile couldn't recognize as the
    same event (bug — traced from production: 46 messages, exactly one with
    empty meta, with a duplicate eid+backfilled copy of the same text)."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        pid, tid = project.id, topic.id
        await session.commit()

    text = "这段话既是Stop的兜底文本也是迟到的MessageDisplay"
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(_LateSpoolScreen(ws.spool_dir(pid, tid), text)),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    async for _ in svc.converse(
        topic_id=tid, author="u", content="做点事", summon=True
    ):
        pass
    await settle_turn(svc, tid)
    # A later turn reconciles the spool — the copy that landed live and the
    # spooled one are the same message, and only one of them may survive.
    async for _ in svc.converse(topic_id=tid, author="u", content="接着", summon=True):
        pass
    await settle_turn(svc, tid)

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(tid)
    matches = [b for b in rows if b.kind == BlockKind.message and b.content == text]
    assert len(matches) == 1  # not duplicated


@pytest.mark.anyio
async def test_duplicate_tool_event_is_deduped_by_event_id(client, tmp_path):
    factory = client.test_factory
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(_DupToolScreen()),
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

    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="做点事", summon=True
    ):
        pass
    await settle_turn(svc, topic_id)
    # The re-delivery is dropped: one event landed, not two.
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    assert len(_event_blocks_for(rows, "dup-1")) == 1


@pytest.mark.anyio
async def test_fallback_dedup_survives_mention_expansion_and_trailing_newline(
    client, tmp_path, monkeypatch
):
    """The sweep's dedup compared the STORED content (mention-expanded, never
    stripped) against the raw result text — an @ or a trailing newline in the
    message defeated the comparison and re-persisted the same text eid-less."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    pid, tid = await _project_topic(factory)

    text = "@u 交给你了\n"
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(_LateSpoolScreen(ws.spool_dir(pid, tid), text)),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    async for _ in svc.converse(
        topic_id=tid, author="u", content="做点事", summon=True
    ):
        pass
    await settle_turn(svc, tid)
    # The later turn is what reconciles the spool.
    async for _ in svc.converse(topic_id=tid, author="u", content="接着", summon=True):
        pass
    await settle_turn(svc, tid)

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(tid)
    matches = [
        b
        for b in rows
        if b.kind == BlockKind.message
        and b.author_type == AuthorType.ai
        and "交给你了" in (b.content or "")
    ]
    assert len(matches) == 1  # one copy, whichever path landed it


# --- MessageDisplay flush coalescing in the reconcile --------------------------


def _spool_flush(
    spool: Path,
    eid: str,
    mid: str,
    idx: int,
    delta: str,
    *,
    final: bool = False,
    age_s: float = 0.0,
) -> None:
    """One MessageDisplay flush file, exactly as the forwarder writes it.

    ``age_s`` backdates the file. How long ago a flush arrived is what decides
    whether an unfinished message is still coming or was abandoned, and that is
    the file's mtime — the name is a sequence number and says nothing about time.
    """
    event_spool.append(
        spool,
        eid,
        {
            "hook_event_name": "MessageDisplay",
            "message_id": mid,
            "index": idx,
            "final": final,
            "delta": delta,
        },
    )
    if age_s:
        written = next(p for p in spool.iterdir() if p.name.endswith(f".{eid}"))
        then = time.time() - age_s
        os.utime(written, (then, then))


async def _project_topic(factory) -> tuple[uuid.UUID, uuid.UUID]:
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        pid, tid = project.id, topic.id
        await session.commit()
    return pid, tid


def _quiet_service(factory, tmp_path) -> ChatService:
    return ChatService(
        session_factory=factory,
        compute=stub_compute(QuietScreen()),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )


def _ai_messages(rows, *, exclude: tuple[str, ...] = ("ok",)) -> list:
    return [
        b
        for b in rows
        if b.kind == BlockKind.message
        and b.author_type == AuthorType.ai
        and b.content not in exclude
    ]


@pytest.mark.anyio
async def test_spooled_message_flushes_land_as_one_block(client, tmp_path, monkeypatch):
    """A lost turn's reply reached the spool as line-batch flushes plus the
    Stop. The backfill must land ONE whole message — not one block per flush
    plus a full-text copy from the Stop, which is exactly the reported
    '断成好几条' + '存两次' shape."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    svc = _quiet_service(factory, tmp_path)
    pid, tid = await _project_topic(factory)
    spool = ws.spool_dir(pid, tid)

    _spool_flush(spool, "f0", "m1", 0, "第一行\n")
    _spool_flush(spool, "f1", "m1", 1, "第二行", final=True)
    spool.mkdir(parents=True, exist_ok=True)
    (spool / "1700000000000000002.s1").write_text(
        json.dumps(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "第一行\n第二行",
                "session_id": "s1",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    async for _ in svc.converse(topic_id=tid, author="u", content="继续", summon=True):
        pass
    await settle_turn(svc, tid)

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(tid)
    messages = _ai_messages(rows)
    assert [b.content for b in messages] == ["第一行\n第二行"]
    assert messages[0].meta.get("backfilled") is True
    assert messages[0].meta.get("eids") == ["f0", "f1"]
    assert _unread(spool) == []


@pytest.mark.anyio
async def test_incomplete_flushes_wait_for_the_missing_one(
    client, tmp_path, monkeypatch
):
    """A message whose final flush has not reached the spool yet must NOT land
    as a fragment: its files stay for the pass where the message completes."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    svc = _quiet_service(factory, tmp_path)
    pid, tid = await _project_topic(factory)
    spool = ws.spool_dir(pid, tid)

    _spool_flush(spool, "f0", "m1", 0, "第一行\n")
    async for _ in svc.converse(
        topic_id=tid, author="u", content="催一下", summon=True
    ):
        pass
    await settle_turn(svc, tid)
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(tid)
    assert _ai_messages(rows) == []
    assert _unread(spool) == ["f0"]

    _spool_flush(spool, "f1", "m1", 1, "第二行", final=True)
    async for _ in svc.converse(topic_id=tid, author="u", content="再催", summon=True):
        pass
    await settle_turn(svc, tid)
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(tid)
    assert [b.content for b in _ai_messages(rows)] == ["第一行\n第二行"]
    assert _unread(spool) == []


class _FlushedMessageScreen(StubChannel):
    """A live hooks turn after coalescing: ONE whole message carrying every
    constituent flush id, then the Stop echoing the same text."""

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        del reply
        self.acknowledges(topic_id, prompt)
        self.hook(
            topic_id,
            hook_event_name="MessageDisplay",
            message_id="m0",
            index=0,
            final=False,
            delta="第一行\n",
            _eid="f0",
        )
        self.hook(
            topic_id,
            hook_event_name="MessageDisplay",
            message_id="m0",
            index=1,
            final=True,
            delta="第二行",
            _eid="f1",
        )
        self.stops(topic_id, "第一行\n第二行", session_id="s1")


@pytest.mark.anyio
async def test_live_coalesced_message_is_not_backfilled_again(
    client, tmp_path, monkeypatch
):
    """The live path persisted the whole message with every flush id; the
    spool still holds the per-flush files. The next reconcile must recognize
    EACH flush id as already materialized — matching only the first one left
    the rest to land again as fragments."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(_FlushedMessageScreen()),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    pid, tid = await _project_topic(factory)
    async for _ in svc.converse(topic_id=tid, author="u", content="说吧", summon=True):
        pass
    await settle_turn(svc, tid)

    spool = ws.spool_dir(pid, tid)
    _spool_flush(spool, "f0", "m1", 0, "第一行\n")
    _spool_flush(spool, "f1", "m1", 1, "第二行", final=True)

    quiet = _quiet_service(factory, tmp_path)
    async for _ in quiet.converse(
        topic_id=tid, author="u", content="继续", summon=True
    ):
        pass
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(tid)
    assert [b.content for b in _ai_messages(rows)] == ["第一行\n第二行"]


@pytest.mark.anyio
async def test_abandoned_partial_lands_joined_after_grace(
    client, tmp_path, monkeypatch
):
    """Flushes whose message never completed (the screen died mid-message, no
    Stop ever spooled) must still land once they are stale — joined into one
    block, not one per flush."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    svc = _quiet_service(factory, tmp_path)
    pid, tid = await _project_topic(factory)
    spool = ws.spool_dir(pid, tid)

    stale = _SPOOL_PARTIAL_GRACE_S + 60
    _spool_flush(spool, "f0", "m1", 0, "只说到一半\n", age_s=stale)
    _spool_flush(spool, "f1", "m1", 1, "然后就断了", age_s=stale)
    async for _ in svc.converse(topic_id=tid, author="u", content="人呢", summon=True):
        pass
    await settle_turn(svc, tid)
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(tid)
    assert [b.content for b in _ai_messages(rows)] == ["只说到一半\n然后就断了"]
    assert _unread(spool) == []


# --- Stop-vs-live dedup across mention canonicalization -----------------------


class _LiveMessageScreen(StubChannel):
    """One complete 芝士 message delivered live, with its own event id."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        del reply
        self.acknowledges(topic_id, prompt)
        self.hook(
            topic_id,
            hook_event_name="MessageDisplay",
            delta=self.text,
            _eid="live-1",
        )
        self.stops(topic_id, "", session_id="s1")


def _spool_stop(spool: Path, eid: str, last_message: str) -> None:
    """The turn-ending Stop, parked because nothing was listening for it."""
    spool.mkdir(parents=True, exist_ok=True)
    (spool / f"1700000000000000001.{eid}").write_text(
        json.dumps({"hook_event_name": "Stop", "last_assistant_message": last_message}),
        encoding="utf-8",
    )


@pytest.mark.parametrize("text", ["我改完了", "@u 交给你了"])
@pytest.mark.anyio
async def test_stop_does_not_duplicate_a_message_that_landed_live(
    client, tmp_path, monkeypatch, text
):
    """A Stop's `last_assistant_message` is a copy of a message already in the
    room, so it must not land again — whether or not that message mentions
    anyone. It has its own event id, so the ONLY guard is the text comparison,
    and a message carrying "@handle" is stored canonicalized (`<@handle>`)
    while the hook payload still holds the friendly form: comparing the two
    raw put an identical-looking second copy in the room."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    pid, tid = await _project_topic(factory)
    async with factory() as session:
        session.add(ProjectMember(project_id=pid, user_handle="u"))
        await session.commit()

    live = ChatService(
        session_factory=factory,
        compute=stub_compute(_LiveMessageScreen(text)),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    async for _ in live.converse(
        topic_id=tid, author="u", content="做点事", summon=True
    ):
        pass

    _spool_stop(ws.spool_dir(pid, tid), "stop-1", text)
    async for _ in _quiet_service(factory, tmp_path).converse(
        topic_id=tid, author="u", content="再来", summon=True
    ):
        pass

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(tid)
    assert len(_ai_messages(rows)) == 1
