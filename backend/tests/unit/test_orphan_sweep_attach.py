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
- one agent in a topic gets at most one remedial prompt, however many
  orphans it holds.

Whether the screen survived used to be unanswerable, so the sweep inferred it:
an AI block bearing the turn's id, an unread record the session left. The
platform can ask now. What is left of the old evidence is one narrow backstop —
a process dying between the transport accepting the write and the record of it.

None of this is announced any more. It used to be, because a restart left the
room looking dead — the backend half died and the session's output only
resurfaced later. Retiring the turn (#508) removed that: the
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

from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent import dispatch_log, turn_inputs
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.agent_session.models import AgentSession
from tests.support.work_chat import WorkChat
from tests.turn_log import a_topic, open_turn, open_turn_ids


class _Chat(WorkChat):
    """ChatService stand-in for sweep flows: serves the evidence probe, records
    events, and any turn the sweep actually submits."""

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
        self.retryable: list[bool] = []
        # The turn each event is about, in step with `events`: the run-record
        # page can only attribute a failure to a turn when this is set.
        self.turn_ids: list[uuid.UUID | None] = []
        self.converse_calls: list[dict] = []

    async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
        self.events.append((topic_id, text))
        self.notices.append((topic_id, text + ((meta or {}).get("detail") or "")))
        self.retryable.append(bool((meta or {}).get("retryable")))
        self.turn_ids.append(turn_id)
        return {"id": "b1", "content": text}

    def has_live_screen(self, topic_id, agent_handle=None):
        return self._live_screen

    def retire_unheard(self, turn_ids):
        pass

    async def turns_that_produced_something(self, turn_ids):
        if self._probe_error:
            raise RuntimeError("probe blew up")
        return {t for t in turn_ids if str(t) in self._delivered}

    def replaying(self, topic_id):
        return None

    async def converse(self, **kw):
        self.converse_calls.append(kw)
        yield {"type": "done"}


@pytest.mark.anyio
async def test_replay_protects_only_its_own_conversation_from_the_orphan_sweep(
    db_factory,
):
    pending_room = await a_topic(db_factory)
    ready_room = await a_topic(db_factory)
    pending_turn = await open_turn(db_factory, pending_room)
    ready_turn = await open_turn(db_factory, ready_room)
    chat = _Chat(db_factory, delivered=[pending_turn, ready_turn])
    replay = asyncio.create_task(asyncio.Event().wait())
    chat.replaying = lambda room: replay if room == pending_room else None
    runner = AgentWorkRunner(InProcessBroker())
    try:
        await runner.resume_orphans(chat)
        assert await open_turn_ids(db_factory) == {pending_turn}
    finally:
        replay.cancel()
        await asyncio.gather(replay, return_exceptions=True)


@pytest.mark.anyio
async def test_exact_terminal_evidence_is_consumed_while_the_room_replays(db_factory):
    topic = await a_topic(db_factory)
    now = datetime.now(UTC)
    turns = {token: uuid.uuid4() for token in ("gone", "unanswered")}
    dispatches = {}
    async with db_factory() as session:
        for token, turn in turns.items():
            sid = uuid.uuid4()
            session.add(
                AgentSession(
                    id=sid,
                    conversation_id=topic,
                    agent_handle=token,
                    harness="claude-code",
                    resume_token=token,
                )
            )
            await AgentTurnRepository(session).open(
                turn_id=turn,
                conversation_id=topic,
                continuation_id=turn,
                author="u",
                content="Run tests",
                is_resume=False,
                resendable=True,
                started_at=now - timedelta(seconds=60),
                delivered_at=now - timedelta(seconds=59),
                agent_handle=token,
                session_id=token,
            )
            dispatches[token] = dispatch_log.record(
                session,
                place_id=topic,
                key=token + "-call",
                tool="Bash",
                session_id=sid,
                lease_generation=uuid.uuid4(),
            )
        await session.commit()
    chat = _Chat(db_factory)
    replay = asyncio.create_task(asyncio.Event().wait())
    chat.replaying = lambda room: replay
    chat.row_is_dead = lambda room, agent, sid: sid == "gone"
    chat.has_live_screen = lambda room, agent=None: agent == "unanswered"
    runner = AgentWorkRunner(InProcessBroker())
    try:
        await runner.resume_orphans(chat)
        assert await open_turn_ids(db_factory) == {turns["unanswered"]}
        assert not chat.converse_calls
        async with db_factory() as session:
            gone = await session.get(dispatch_log.DispatchRow, dispatches["gone"])
            healthy = await session.get(
                dispatch_log.DispatchRow, dispatches["unanswered"]
            )
            assert gone.outcome == "unknown"
            assert healthy.outcome is None
            await dispatch_log.settle(session, healthy.id, dispatch_log.Outcome.done)
            await session.commit()
        async with db_factory() as session:
            healthy = await session.get(
                dispatch_log.DispatchRow, dispatches["unanswered"]
            )
            assert healthy.outcome == "done"
        assert len(chat.notices) == 1
        assert "gone-call" in chat.notices[0][1]
        assert "unanswered-call" not in chat.notices[0][1]
    finally:
        replay.cancel()
        await asyncio.gather(replay, return_exceptions=True)


@pytest.mark.anyio
async def test_overlapping_sweeps_resend_an_unheard_prompt_only_once(
    db_factory, monkeypatch
):
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    await open_turn(db_factory, topic, content="Fix the login page", age_s=90)
    chat = _Chat(db_factory)
    runner = AgentWorkRunner(InProcessBroker())
    both_read = asyncio.Event()
    reads = 0
    original = turn_inputs.open_turns

    async def read_together(factory):
        nonlocal reads
        turns = await original(factory)
        reads += 1
        if reads == 2:
            both_read.set()
        await both_read.wait()
        return turns

    monkeypatch.setattr(turn_inputs, "open_turns", read_together)
    results = await asyncio.wait_for(
        asyncio.gather(runner.resume_orphans(chat), runner.sweep_orphans(chat)), 5
    )
    assert sum(results) == 1
    await _drain(chat)
    assert [call["content"] for call in chat.converse_calls] == ["Fix the login page"]


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
    write a block and its first records had not arrived — so judging by that
    alone re-sends a prompt 芝士 was already working on, and the person gets
    answered twice."""
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    await open_turn(db_factory, topic, age_s=90, delivered=True)
    chat = _Chat(db_factory)  # no AI block: the old evidence sees nothing
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 0
    await _drain(chat, rounds=50)
    assert chat.converse_calls == []  # NOT re-sent
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
    """No block, no trace → the task never arrived. The re-sent turn
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
    assert chat.events == []  # the platform handled it; nothing to explain


@pytest.mark.anyio
async def test_delivered_and_undelivered_split_gets_both_remedies(
    db_factory, monkeypatch
):
    """A running turn (delivered) plus a queued human message (never sent,
    no trace): the delivered one is attached, the undelivered one is re-sent —
    still one prompt total."""
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    running = await open_turn(db_factory, topic, content="老任务", age_s=600)
    await open_turn(db_factory, topic, content="新消息", age_s=120)
    chat = _Chat(db_factory, delivered=[running])
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 1
    await _drain(chat)
    assert len(chat.converse_calls) == 1
    assert chat.converse_calls[0]["content"] == "新消息"
    assert chat.events == []


@pytest.mark.anyio
async def test_a_wedged_turn_is_cancelled_and_never_re_run(db_factory, monkeypatch):
    """A wedged turn is torn down and handed to a person — never re-run.

    Re-running it only re-enters the machine that just died under it, so the
    sweep cancels the corpse (to free the topic lock) and says so once. Driven
    end to end: what a person in the room sees is that 芝士 did NOT silently
    restart, and that the notice tells them it is now on them.
    """
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    turn = await open_turn(db_factory, topic, age_s=90)

    chat = _Chat(db_factory)
    runner = AgentWorkRunner(InProcessBroker())

    # A turn whose task is alive but silent on both signals — what the sweep
    # calls wedged.
    async def _never():
        await asyncio.Event().wait()

    task = asyncio.create_task(_never())
    await asyncio.sleep(0)
    runner._live[str(turn)] = task
    runner._last_frame_at[str(turn)] = time.monotonic() - 4000

    async def _last_activity(_topics):
        return {topic: datetime.now(UTC) - timedelta(seconds=4000)}

    # Zero remedial re-sends: a wedged turn is cancelled and announced, never re-run.
    assert await runner.sweep_orphans(chat, last_activity=_last_activity) == 0
    for _ in range(400):
        await asyncio.sleep(0)

    assert task.cancelled() or task.cancelling(), "the wedged turn was not torn down"
    assert chat.converse_calls == [], (
        f"卡死的轮次被又跑了 {len(chat.converse_calls)} 次 —— 平台不该自动重跑"
    )
    # Exactly one notice, and it hands the topic to a person rather than
    # promising the platform will pick it back up.
    assert len(chat.events) == 1
    assert chat.retryable == [True]
    # The notice names the turn it is about. Without the id the run-record page
    # reads a turn that timed out as one that never failed (`run_record/turns.py`).
    assert chat.turn_ids == [turn]


@pytest.mark.anyio
async def test_a_stranded_turn_is_announced_with_its_turn_id(db_factory, monkeypatch):
    """The deploy-interrupted notice is about one turn — the newest stranded
    one — and carries its id for the same reason as the wedged notice: a turn
    whose only failure event has no `turn_id` looks like it never failed."""
    _instant_sleep(monkeypatch)
    topic = await a_topic(db_factory)
    # Two re-sends the deploy interrupted: nothing will re-send either again, so
    # the room is handed the decision instead of a silent retry. The notice is
    # about the NEWEST of them, which is the one the verdict was read from.
    await open_turn(
        db_factory,
        topic,
        content="更早的一轮",
        age_s=600,
        is_resume=True,
        resendable=False,
    )
    newest = await open_turn(
        db_factory,
        topic,
        content="重发的一轮",
        age_s=90,
        is_resume=True,
        resendable=False,
    )
    chat = _Chat(db_factory)
    runner = AgentWorkRunner(InProcessBroker())

    assert await runner.resume_orphans(chat) == 0
    await _drain(chat, rounds=50)
    assert chat.converse_calls == []  # no automatic re-run
    assert len(chat.events) == 1
    assert chat.turn_ids == [newest]


@pytest.mark.parametrize(
    "state", ["pending", "claimed", "sending", "uncertain", "received"]
)
async def test_delivery_attempt_is_never_replayed_by_generic_orphan_recovery(
    db_factory, monkeypatch, state
):
    from unittest.mock import Mock

    from app.domain.delivery.models import Delivery

    topic_id = await a_topic(db_factory)
    turn_id = await open_turn(db_factory, topic_id, resendable=True)
    now = datetime.now(UTC)
    async with db_factory() as session:
        session.add(
            Delivery(
                event_id=uuid.uuid4(),
                recipient_handle="agent-recipient",
                receiver_id=None,
                conversation_id=topic_id,
                attempt_id=turn_id,
                state=state,
                dedup_key=str(uuid.uuid4()),
                type="agent",
                payload={},
                event_at=now,
                recorded_at=now,
            )
        )
        await session.commit()
    runner = AgentWorkRunner(InProcessBroker())
    schedule = Mock()
    monkeypatch.setattr(runner, "_schedule_resend", schedule)
    chat = _Chat(db_factory)
    assert await runner.resume_orphans(chat) == 0
    schedule.assert_not_called()
    assert chat.events == []
    async with db_factory() as session:
        from sqlalchemy import select

        row = await session.scalar(
            select(Delivery).where(Delivery.attempt_id == turn_id)
        )
        assert row.state == state
