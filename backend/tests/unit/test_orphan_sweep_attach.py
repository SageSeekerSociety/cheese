"""Orphan sweep: adopt what is still running, re-prompt only what reached nobody.

The incident behind this (#316's root-cause side): a backend restart kills only
the coroutine WAITING on a turn — the claude out in the execution environment
survives and keeps working. The old sweep re-prompted every orphan to "接着干",
which stacked five zombie turns on one topic in a single day. The contract now:

- the screen is still there AND the prompt reached it → the turn is not an
  orphan at all. It is left running and its interval left open, to be closed by
  the Stop that screen eventually sends;
- nobody heard it → re-send the ORIGINAL prompt text, once, whoever started the
  turn — a person's message and 平台's own work (分身开工, 验收卡被驳回, CI 红了)
  evaporate identically when the prompt never lands;
- one topic gets at most one remedial prompt, however many orphans it holds.

Whether the screen survived used to be unanswerable, so the sweep inferred it:
an AI block bearing the turn's id, an unread hook in the topic's spool. The
platform can ask now. What is left of the old evidence is one narrow backstop —
a process dying between the transport accepting the write and the record of it.

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
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker
from tests.turn_log import a_topic, open_turn, open_turn_ids


class _Chat:
    """ChatService stand-in for sweep flows: serves the evidence probe, records
    events, settle scheduling, and any turn the sweep actually submits."""

    def __init__(self, factory, *, delivered=(), live_screen=False, probe_error=False):
        # The sweep reads and closes turn intervals through this, the same way
        # the real ChatService hands the runner its database.
        self.session_factory = factory
        self._delivered = {str(t) for t in delivered}
        self._live_screen = live_screen
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

    def has_live_screen(self, topic_id):
        return self._live_screen

    async def turns_that_produced_something(self, turn_ids):
        if self._probe_error:
            raise RuntimeError("probe blew up")
        return {t for t in turn_ids if str(t) in self._delivered}

    def schedule_spool_settle(self, topic_id, delay_s=2.0):
        self.settled.append(topic_id)

    async def converse(self, **kw):
        self.converse_calls.append(kw)
        yield {"type": "done"}


# Captured before any test patches `asyncio.sleep` away. `_drain` has to wait on
# real time now: the path it is waiting for opens a turn interval in the
# database, and a loop spun with sleep(0) never gives that round-trip a chance.
_REAL_SLEEP = asyncio.sleep


def _instant_sleep(monkeypatch) -> None:
    async def _instant(_delay, *a, **k):
        await _REAL_SLEEP(0)

    monkeypatch.setattr(asyncio, "sleep", _instant)


async def _drain(chat: _Chat, rounds: int = 300) -> None:
    """Give scheduled re-send tasks (sleep → submit → converse) time to land."""
    for _ in range(rounds):
        await _REAL_SLEEP(0.01)
        if chat.converse_calls:
            return


@pytest.mark.anyio
async def test_a_turn_that_produced_something_is_never_reprompted(
    db_factory, monkeypatch
):
    """No screen answers for this topic any more, but the turn left an AI block
    behind — so 芝士 did hear the task. Re-sending it would be asking twice for
    work already done. Drain whatever is parked and say nothing."""
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    turn = await open_turn(db_factory, topic, age_s=90)
    chat = _Chat(db_factory, delivered=[turn])
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 0
    await _drain(chat, rounds=50)
    assert chat.converse_calls == []  # no new prompt of any kind
    assert chat.settled == [topic]  # the settle collects what claude sends
    # Nothing is said: the subscription reattaches on restart (#508), so the
    # survivor's output keeps landing in the room on its own and there is no
    # break for the room to explain.
    assert chat.events == []
    # claimed — the interval is closed, so no re-announce next sweep
    assert await open_turn_ids(db_factory) == set()


@pytest.mark.anyio
async def test_a_delivery_stamp_beats_having_produced_nothing_yet(
    db_factory, monkeypatch
):
    """The prompt landed two seconds before the process died, and the screen is
    gone by the time the sweep runs (the container went with the deploy).

    The transport accepted the write, so that fact was recorded when it
    happened. The second-hand evidence cannot see it — claude had no time to
    write a block and its first hooks had not arrived — so judging by that
    alone re-sends a prompt 芝士 was already working on, and the person gets
    answered twice."""
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    await open_turn(db_factory, topic, age_s=90, delivered=True)
    chat = _Chat(db_factory)  # no AI block, empty spool: the old evidence sees nothing
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 0
    await _drain(chat, rounds=50)
    assert chat.converse_calls == []  # NOT re-sent
    assert chat.settled == [topic]  # attached instead
    assert chat.events == []


@pytest.mark.anyio
async def test_a_surviving_screen_is_adopted_rather_than_swept(db_factory, monkeypatch):
    """The deploy case, end to end: the backend was replaced, the screen was not.

    Both turns reached that screen, so neither is an orphan — nothing is
    prompted, nothing is announced, and both intervals stay OPEN, because what
    ends them is the Stop the screen will send, not the sweep."""
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    first = await open_turn(
        db_factory, topic, content="任务甲", age_s=120, delivered=True
    )
    second = await open_turn(
        db_factory, topic, content="任务乙", age_s=80, delivered=True
    )
    chat = _Chat(db_factory, live_screen=True)
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 0
    await _drain(chat, rounds=50)
    assert chat.converse_calls == []
    assert chat.settled == []  # nothing was interrupted, so nothing to collect
    assert chat.events == []
    assert await open_turn_ids(db_factory) == {first, second}


@pytest.mark.anyio
async def test_a_surviving_screen_that_never_heard_the_prompt_is_not_adopted(
    db_factory, monkeypatch
):
    """A screen being alive is not enough. The prompt died in the gap between
    the person pressing send and the transport accepting the write, so that
    screen is sitting idle — adopting it would wait forever for a Stop nobody
    is going to send."""
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    stranded = await open_turn(db_factory, topic, content="修一下登录页", age_s=90)
    chat = _Chat(db_factory, live_screen=True)
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 1
    await _drain(chat)
    assert [call["content"] for call in chat.converse_calls] == ["修一下登录页"]
    # Claimed, so the next sweep does not send it a third time. (The re-send is
    # itself a turn and opens an interval of its own, which is why this asks
    # about the swept one by id rather than for an empty set.)
    assert stranded not in await open_turn_ids(db_factory)


@pytest.mark.anyio
async def test_zero_evidence_resends_the_original_prompt_once(db_factory, monkeypatch):
    """No block, no spool trace → the task never arrived. The re-sent turn
    carries the ORIGINAL text (is_resume, so it can never chain further)."""
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    await open_turn(db_factory, topic, content="修一下登录页", age_s=90)
    chat = _Chat(db_factory)
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
async def test_five_orphans_one_topic_get_at_most_one_action(db_factory, monkeypatch):
    """The incident shape: five orphans on one topic. One re-send (the newest
    human turn), one event; the rest are folded in — never five prompts."""
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    for i in range(5):
        await open_turn(db_factory, topic, content=f"任务{i}", age_s=600 - i * 60)
    chat = _Chat(db_factory)
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 1
    await _drain(chat)
    assert len(chat.converse_calls) == 1
    assert chat.converse_calls[0]["content"] == "任务4"  # the newest one
    # 一条都不说：五轮的消息都随这一次重发带上了，用户不需要做任何事。
    assert chat.events == []


@pytest.mark.anyio
async def test_probe_failure_is_treated_as_evidence(db_factory, monkeypatch):
    """DB down mid-sweep: with no way to prove non-delivery, speaking is the
    dangerous side — attach, never re-prompt on a guess."""
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    await open_turn(db_factory, topic, age_s=90)
    chat = _Chat(db_factory, probe_error=True)
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 0
    await _drain(chat, rounds=50)
    assert chat.converse_calls == []
    assert chat.settled == [topic]
    assert chat.events == []  # the platform handled it; nothing to explain


@pytest.mark.anyio
async def test_delivered_and_undelivered_split_gets_both_remedies(
    db_factory, monkeypatch
):
    """A running turn (delivered) plus a queued human message (never sent, spool
    clean): the delivered one is attached, the undelivered one is re-sent —
    still one prompt total."""
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    running = await open_turn(db_factory, topic, content="老任务", age_s=600)
    await open_turn(db_factory, topic, content="新消息", age_s=120)
    chat = _Chat(db_factory, delivered=[running])
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 1
    await _drain(chat)
    assert chat.settled == [topic]
    assert len(chat.converse_calls) == 1
    assert chat.converse_calls[0]["content"] == "新消息"
    assert chat.events == []


@pytest.mark.anyio
async def test_a_wedged_resume_spends_from_the_same_budget(db_factory, monkeypatch):
    """卡死清扫排的那次续跑，算进同一份预算里 (#574).

    The sweep's remedy for a wedged turn IS an automatic continuation, so it has
    to count as one. Counting it as zero hands the chain a fresh budget: the
    platform can announce 「不再自动重试」 and then, because the next failure
    entered through the sweep instead of the crash handler, quietly start
    retrying again — taking its own handover back.

    Driven end to end rather than by inspecting the counter: what a person in
    the room sees is how many times 芝士 restarted, and that is what is asserted.
    """
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    turn = await open_turn(db_factory, topic, age_s=90)

    class _BrokenChat(_Chat):
        """Every turn after the sweep's fails the way dev's 257 did: an
        exception no classifier recognises."""

        async def converse(self, **kw):
            self.converse_calls.append(kw)
            raise RuntimeError("boom")
            yield  # pragma: no cover — makes this an async generator

    chat = _BrokenChat(db_factory)
    runner = AgentWorkRunner(InProcessBroker())

    # A turn whose task is alive but silent on both signals — what the sweep
    # calls wedged, and the only path that reaches the resume under test.
    async def _never():
        await asyncio.Event().wait()

    task = asyncio.create_task(_never())
    await asyncio.sleep(0)
    runner._live[str(turn)] = task
    runner._last_frame_at[str(turn)] = time.monotonic() - 4000

    async def _last_activity(_topics):
        return {topic: datetime.now(UTC) - timedelta(seconds=4000)}

    assert await runner.sweep_orphans(chat, last_activity=_last_activity) == 1
    for _ in range(400):
        await asyncio.sleep(0)

    assert len(chat.converse_calls) <= AgentWorkRunner.MAX_RESUME_CHAIN, (
        f"卡死续跑后又跑了 {len(chat.converse_calls)} 轮 —— "
        f"清扫排的那次没算进预算，等于多给了一轮重试"
    )
