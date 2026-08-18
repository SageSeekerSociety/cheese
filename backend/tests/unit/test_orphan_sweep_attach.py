"""Orphan sweep: attach by default, re-prompt only on proof of non-delivery.

The incident behind this (#316's root-cause side): a backend restart kills only
the coroutine WAITING on a turn — the claude out in the execution environment
survives and keeps working. The old sweep re-prompted every orphan to "接着干",
which stacked five zombie turns on one topic in a single day. The contract now:

- any evidence claude received the task (an AI block on the turn, or anything
  in the topic's spool) → NO new prompt; the spool settle collects what the
  survivor sends back (its Stop included);
- zero evidence anywhere → re-send the ORIGINAL prompt text, once, whoever
  started the turn — a person's message and 平台's own work (分身开工, 验收卡被
  驳回, CI 红了) evaporate identically when the prompt never lands;
- one topic gets at most one remedial prompt, however many orphans it holds.

None of this is announced any more. It used to be, because a restart left the
room looking dead — the backend half died and the session's output only
resurfaced later out of the spool. Retiring the turn (#508) removed that: the
subscription lives with the screen and reattaches, so the room keeps showing
芝士 working. The bar for speaking is not "was there an interruption" but "will
this still be broken after the platform finishes" — so what remains announced is
only the case where the prompt is gone and nothing will re-send it (see
`test_runtime`).
"""

import asyncio
import time as _time
import uuid

import pytest

from app.domain.agent import runtime as rt
from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker


class _Chat:
    """ChatService stand-in for sweep flows: serves the evidence probe, records
    events, settle scheduling, and any turn the sweep actually submits."""

    def __init__(self, *, delivered=(), spool=False, probe_error=False):
        self._delivered = {str(t) for t in delivered}
        self._spool = spool
        self._probe_error = probe_error
        self.events: list[tuple[uuid.UUID, str]] = []
        # 平台提示统一契约: 房间里的一行是 `text`，展开才看的长文在 meta.detail。
        self.notices: list[tuple[uuid.UUID, str]] = []
        self.settled: list[uuid.UUID] = []
        self.converse_calls: list[dict] = []

    async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
        self.events.append((topic_id, text))
        self.notices.append((topic_id, text + ((meta or {}).get("detail") or "")))
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
    resendable: bool = True,
) -> dict:
    return {
        "topic_id": str(topic),
        "started_at": _time.time() - age_s,
        "is_resume": is_resume,
        "author": author,
        "content": content,
        # Decided where the turn starts (`_execute`): true for anything whose
        # content IS the task, false for an auto-resume nudge.
        "resendable": resendable,
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
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 0
    await _drain(chat, rounds=50)
    assert chat.converse_calls == []  # no new prompt of any kind
    assert chat.settled == [topic]  # the settle collects what claude sends
    # Nothing is said: the subscription reattaches on restart (#508), so the
    # survivor's output keeps landing in the room on its own and there is no
    # break for the room to explain.
    assert chat.events == []
    assert rt._load_inflight() == {}  # claimed — no re-announce next sweep


@pytest.mark.anyio
async def test_a_delivery_stamp_beats_having_produced_nothing_yet(
    tmp_path, monkeypatch
):
    """The prompt landed two seconds before the process died.

    The transport accepted the write, so that fact was recorded when it
    happened. The second-hand evidence cannot see it — claude had no time to
    write a block and its first hooks had not arrived — so judging by that
    alone re-sends a prompt 芝士 is already working on, and the person gets
    answered twice."""
    _instant_sleep(monkeypatch)
    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    topic, turn = uuid.uuid4(), uuid.uuid4()
    entry = _entry(topic, age_s=90)
    entry["delivered_at"] = _time.time() - 88
    rt._save_inflight({str(turn): entry})
    chat = _Chat()  # no AI block, empty spool: the old evidence sees nothing
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 0
    await _drain(chat, rounds=50)
    assert chat.converse_calls == []  # NOT re-sent
    assert chat.settled == [topic]  # attached instead
    assert chat.events == []


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
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 0
    await _drain(chat, rounds=50)
    assert chat.converse_calls == []
    assert chat.settled == [topic]
    assert chat.events == []  # the platform handled it; nothing to explain


@pytest.mark.anyio
async def test_zero_evidence_resends_the_original_prompt_once(tmp_path, monkeypatch):
    """No block, no spool trace → the task never arrived. The re-sent turn
    carries the ORIGINAL text (is_resume, so it can never chain further)."""
    _instant_sleep(monkeypatch)
    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    topic = uuid.uuid4()
    rt._save_inflight({str(uuid.uuid4()): _entry(topic, content="修一下登录页")})
    chat = _Chat()
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 1
    await _drain(chat)
    assert len(chat.converse_calls) == 1
    call = chat.converse_calls[0]
    assert call["content"] == "修一下登录页"  # 原文, not a "接着干" nudge
    assert call["author"] == "system"
    assert call["is_resume"] is True
    assert chat.settled == []  # nothing to attach to
    # The re-send happens, and says nothing: it lands in the same session the
    # person was already talking to, so it is indistinguishable from them
    # asking again — there is no anomaly to narrate.
    assert chat.notices == []


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
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 1
    await _drain(chat)
    assert len(chat.converse_calls) == 1
    assert chat.converse_calls[0]["content"] == "任务4"  # the newest one
    # 一条都不说：五轮的消息都随这一次重发带上了，用户不需要做任何事。
    assert chat.events == []


@pytest.mark.anyio
async def test_probe_failure_is_treated_as_evidence(tmp_path, monkeypatch):
    """DB down mid-sweep: with no way to prove non-delivery, speaking is the
    dangerous side — attach, never re-prompt on a guess."""
    _instant_sleep(monkeypatch)
    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    topic = uuid.uuid4()
    rt._save_inflight({str(uuid.uuid4()): _entry(topic)})
    chat = _Chat(probe_error=True)
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 0
    await _drain(chat, rounds=50)
    assert chat.converse_calls == []
    assert chat.settled == [topic]
    assert chat.events == []  # the platform handled it; nothing to explain


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
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 1
    await _drain(chat)
    assert chat.settled == [topic]
    assert len(chat.converse_calls) == 1
    assert chat.converse_calls[0]["content"] == "新消息"
    assert chat.events == []
