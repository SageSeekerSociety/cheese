"""Orphan sweep: attach by default, re-prompt only on proof of non-delivery.

The incident behind this (#316's root-cause side): a backend restart kills only
the coroutine WAITING on a turn — the claude out in the execution environment
survives and keeps working. The old sweep re-prompted every orphan to "接着干",
which stacked five zombie turns on one topic in a single day. The contract now:

- any evidence claude received the task (an AI block on the turn, or anything
  in the topic's spool) → NO new prompt; the spool settle collects what the
  survivor sends back (its Stop included);
- zero evidence anywhere → re-send the ORIGINAL prompt text, once;
- one topic gets at most one remedial prompt, however many orphans it holds.
"""

import asyncio
import time as _time
import uuid

import pytest

from app.domain.agent import runtime as rt
from app.domain.agent.runtime import InProcessBroker, TurnRunner


class _Chat:
    """ChatService stand-in for sweep flows: serves the evidence probe, records
    events, settle scheduling, and any turn the sweep actually submits."""

    def __init__(self, *, delivered=(), spool=False, probe_error=False):
        self._delivered = {str(t) for t in delivered}
        self._spool = spool
        self._probe_error = probe_error
        self.events: list[tuple[uuid.UUID, str]] = []
        self.settled: list[uuid.UUID] = []
        self.converse_calls: list[dict] = []

    async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
        self.events.append((topic_id, text))
        return {"id": "b1", "content": text}

    async def orphan_turn_evidence(self, topic_id, turn_ids):
        if self._probe_error:
            raise RuntimeError("probe blew up")
        return {
            "delivered": {t for t in turn_ids if str(t) in self._delivered},
            "spool": self._spool,
        }

    def schedule_spool_settle(self, topic_id, delay_s=2.0):
        self.settled.append(topic_id)

    async def converse(self, **kw):
        self.converse_calls.append(kw)
        yield {"type": "done"}


def _entry(
    topic: uuid.UUID,
    *,
    author: str = "u",
    content: str = "修一下登录页",
    age_s: float = 90,
    is_resume: bool = False,
) -> dict:
    return {
        "topic_id": str(topic),
        "started_at": _time.time() - age_s,
        "is_resume": is_resume,
        "author": author,
        "content": content,
    }


def _instant_sleep(monkeypatch) -> None:
    real_sleep = asyncio.sleep

    async def _instant(_delay, *a, **k):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _instant)


async def _drain(chat: _Chat, rounds: int = 300) -> None:
    """Give scheduled re-send tasks (sleep → submit → converse) time to land."""
    for _ in range(rounds):
        await asyncio.sleep(0)
        if chat.converse_calls:
            return


@pytest.mark.anyio
async def test_delivered_orphan_attaches_instead_of_reprompting(tmp_path, monkeypatch):
    """Hook evidence on the turn → the sweep says so, schedules the spool
    settle, and sends NOTHING — the survivor finishes on its own."""
    _instant_sleep(monkeypatch)
    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    topic, turn = uuid.uuid4(), uuid.uuid4()
    rt._save_inflight({str(turn): _entry(topic)})
    chat = _Chat(delivered=[turn])
    runner = TurnRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 0
    await _drain(chat, rounds=50)
    assert chat.converse_calls == []  # no new prompt of any kind
    assert chat.settled == [topic]  # the settle collects what claude sends
    assert len(chat.events) == 1
    assert "部署中断" in chat.events[0][1]
    assert rt._load_inflight() == {}  # claimed — no re-announce next sweep


@pytest.mark.anyio
async def test_spool_trace_attaches_and_vetoes_every_resend(tmp_path, monkeypatch):
    """A Stop (or any non-SessionStart hook) parked in the topic's spool proves
    a claude has been talking. It carries no turn id, so it vetoes re-sending
    ANY of the topic's orphans — the settle lands it instead."""
    _instant_sleep(monkeypatch)
    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    topic = uuid.uuid4()
    rt._save_inflight(
        {
            str(uuid.uuid4()): _entry(topic, content="任务甲", age_s=120),
            str(uuid.uuid4()): _entry(topic, content="任务乙", age_s=80),
        }
    )
    chat = _Chat(spool=True)
    runner = TurnRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 0
    await _drain(chat, rounds=50)
    assert chat.converse_calls == []
    assert chat.settled == [topic]
    assert len(chat.events) == 1


@pytest.mark.anyio
async def test_zero_evidence_resends_the_original_prompt_once(tmp_path, monkeypatch):
    """No block, no spool trace → the task never arrived. The re-sent turn
    carries the ORIGINAL text (is_resume, so it can never chain further)."""
    _instant_sleep(monkeypatch)
    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    topic = uuid.uuid4()
    rt._save_inflight({str(uuid.uuid4()): _entry(topic, content="修一下登录页")})
    chat = _Chat()
    runner = TurnRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 1
    await _drain(chat)
    assert len(chat.converse_calls) == 1
    call = chat.converse_calls[0]
    assert call["content"] == "修一下登录页"  # 原文, not a "接着干" nudge
    assert call["author"] == "system"
    assert call["is_resume"] is True
    assert chat.settled == []  # nothing to attach to
    assert any("重发" in text for _tid, text in chat.events)


@pytest.mark.anyio
async def test_five_orphans_one_topic_get_at_most_one_action(tmp_path, monkeypatch):
    """The incident shape: five orphans on one topic. One re-send (the newest
    human turn), one event; the rest are folded in — never five prompts."""
    _instant_sleep(monkeypatch)
    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    topic = uuid.uuid4()
    reg = {
        str(uuid.uuid4()): _entry(topic, content=f"任务{i}", age_s=600 - i * 60)
        for i in range(5)
    }
    rt._save_inflight(reg)
    chat = _Chat()
    runner = TurnRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 1
    await _drain(chat)
    assert len(chat.converse_calls) == 1
    assert chat.converse_calls[0]["content"] == "任务4"  # the newest one
    assert len(chat.events) == 1  # one verdict, not five
    assert "4" in chat.events[0][1]  # the other four are named, not silent


@pytest.mark.anyio
async def test_probe_failure_is_treated_as_evidence(tmp_path, monkeypatch):
    """DB down mid-sweep: with no way to prove non-delivery, speaking is the
    dangerous side — attach, never re-prompt on a guess."""
    _instant_sleep(monkeypatch)
    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    topic = uuid.uuid4()
    rt._save_inflight({str(uuid.uuid4()): _entry(topic)})
    chat = _Chat(probe_error=True)
    runner = TurnRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 0
    await _drain(chat, rounds=50)
    assert chat.converse_calls == []
    assert chat.settled == [topic]
    assert len(chat.events) == 1


@pytest.mark.anyio
async def test_delivered_and_undelivered_split_gets_both_remedies(
    tmp_path, monkeypatch
):
    """A running turn (delivered) plus a queued human message (never sent, spool
    clean): the delivered one is attached, the undelivered one is re-sent —
    still one prompt total."""
    _instant_sleep(monkeypatch)
    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    topic = uuid.uuid4()
    running = uuid.uuid4()
    rt._save_inflight(
        {
            str(running): _entry(topic, content="老任务", age_s=600),
            str(uuid.uuid4()): _entry(topic, content="新消息", age_s=120),
        }
    )
    chat = _Chat(delivered=[running])
    runner = TurnRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 1
    await _drain(chat)
    assert chat.settled == [topic]
    assert len(chat.converse_calls) == 1
    assert chat.converse_calls[0]["content"] == "新消息"
    assert len(chat.events) == 1
