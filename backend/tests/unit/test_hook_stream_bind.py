"""`_bind_user_entry` (FB-56): the takeover's memory side survives a
commit abort, and a replay completes both sides. And the active-source
guard: an entry from a session the seat no longer runs moves nothing."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from app.domain.agent.hook_stream import _bind_user_entry
from app.domain.agent.live_work import HookWorkState
from app.domain.agent.service import AgentUserEntry
from app.domain.agent.turn_inputs import (
    bind,
    record_input,
    transition,
)
from app.domain.agent_session.services import AgentSessionService
from tests.turn_log import a_topic, open_turn, turn_row

_NOW = datetime(2026, 10, 1, tzinfo=UTC)
GENERATION = uuid.uuid4()


class _Runner:
    def __init__(self) -> None:
        self.closed: list[uuid.UUID] = []

    def note_session_output(self, turn_id, *, tool: bool) -> None:
        pass

    def close_turn_the_session_started(self, turn_id) -> None:
        self.closed.append(turn_id)


def _event(
    text: str, *, entry_id: str, pos: int, session_id=None, harness=None
) -> AgentUserEntry:
    return AgentUserEntry(
        text,
        entry_id=entry_id,
        pos=pos,
        generation=GENERATION.hex,
        session_id=session_id,
        harness=harness,
        eid=f"pi:user:{entry_id}",
    )


def _state(topic_id, work_id, *, self_started: bool) -> HookWorkState:
    return HookWorkState(
        project_id=uuid.uuid4(),
        topic_id=topic_id,
        work_id=work_id,
        pending_ids=set(),
        reply_to=None,
        roster=None,
        topic_refs=[],
        continuation_id=work_id,
        route="gateway",
        acting_agent="cheese",
        agent_pool=None,
        user_text="",
        started_at=_NOW,
        self_started=self_started,
    )


async def _crown(factory, topic, seat, turn_id, nonce, entry_id, pos):
    """Bind and transition one work into the seat's owner, committed."""
    async with factory() as session:
        row = await bind(
            session,
            nonce=nonce,
            entry_id=entry_id,
            pos=pos,
            generation=GENERATION,
            at=_NOW,
        )
        await transition(
            session,
            topic_id=topic,
            agent_handle=seat,
            session_id="sess-1",
            generation=GENERATION,
            work_id=turn_id,
            input_id=row.id,
            entry_id=entry_id,
            pos=pos,
            at=_NOW,
        )
        await session.commit()


async def _seed(factory, topic, turn_id, nonce):
    async with factory() as session:
        await record_input(session, turn_id=turn_id, nonce=nonce, harness="pi", at=_NOW)
        await session.commit()


class _FailOnceCommit:
    """A session factory whose first commit aborts; everything else is real."""

    def __init__(self, factory) -> None:
        self._factory = factory
        self.failed = False

    def __call__(self):
        inner = self._factory()
        outer = self

        class _Session:
            async def __aenter__(self):
                self._session = await inner.__aenter__()
                if not outer.failed:
                    real_commit = self._session.commit

                    async def boom():
                        outer.failed = True

                        async def noop():
                            pass

                        self._session.commit = noop
                        await self._session.rollback()
                        raise RuntimeError("simulated commit abort")

                    self._session.commit = boom  # type: ignore[method-assign]
                    self._real_commit = real_commit
                return self._session

            async def __aexit__(self, *exc):
                return await inner.__aexit__(*exc)

        return _Session()


@pytest.mark.anyio
async def test_a_commit_abort_keeps_both_sides_and_a_replay_finishes(db_factory):
    """The takeover's commit aborts: the database rolls the bind and the
    retirement back, and the predecessor's hook state and runner marks are
    still there. The replay binds again, commits, and only then drops them
    — once, idempotently."""
    topic = await a_topic(db_factory)
    seat = "cheese"
    turn_s = await open_turn(db_factory, topic, delivered=True)
    turn_t = await open_turn(db_factory, topic, delivered=True)
    nonce_s = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    nonce_t = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    await _seed(db_factory, topic, turn_s, nonce_s)
    await _seed(db_factory, topic, turn_t, nonce_t)
    await _crown(db_factory, topic, seat, turn_s, nonce_s, "e1", 1)

    hook_work = {(topic, turn_s): _state(topic, turn_s, self_started=True)}
    runner = _Runner()
    event = _event(f"接管 {nonce_t}", entry_id="e2", pos=2)

    factory = _FailOnceCommit(db_factory)
    with pytest.raises(RuntimeError, match="commit abort"):
        await _bind_user_entry(
            factory, hook_work, runner, topic_id=topic, seat=seat, event=event
        )

    from sqlalchemy import select

    from app.domain.agent.turn_inputs import AgentTurnInput as _Input

    async with db_factory() as session:
        row = await session.scalar(select(_Input).where(_Input.nonce == nonce_t))
    assert row.state != "bound", "commit 失败：绑定整体回滚"
    s_row = await turn_row(db_factory, turn_s)
    assert s_row.stopped_at is None, "commit 失败：退休整体回滚"
    assert (topic, turn_s) in hook_work, "commit 失败：内存 state 不丢"
    assert runner.closed == [], "commit 失败：runner marks 不动"

    # The replay binds again, commits, and only then drops the memory side.
    await _bind_user_entry(
        db_factory, hook_work, runner, topic_id=topic, seat=seat, event=event
    )
    s_row = await turn_row(db_factory, turn_s)
    t_row = await turn_row(db_factory, turn_t)
    assert s_row.stopped_at is not None
    assert t_row.stopped_at is None
    assert (topic, turn_s) not in hook_work, "commit 后清理 self-started state"
    assert runner.closed == [turn_s], "commit 后清 runner marks"

    # A second replay changes nothing: the cleanup is idempotent.
    await _bind_user_entry(
        db_factory, hook_work, runner, topic_id=topic, seat=seat, event=event
    )
    assert runner.closed == [turn_s]


@pytest.mark.anyio
async def test_an_entry_from_a_superseded_session_moves_nothing(db_factory):
    """The seat's pointer has moved to a new session; an entry draining
    from the old one does not bind, does not transition, and leaves the
    ledger, the owner and the rows exactly as they were."""
    topic = await a_topic(db_factory)
    seat = "cheese"
    turn_s = await open_turn(db_factory, topic, delivered=True)
    turn_t = await open_turn(db_factory, topic, delivered=True)
    nonce_s = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    nonce_t = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    await _seed(db_factory, topic, turn_s, nonce_s)
    await _seed(db_factory, topic, turn_t, nonce_t)
    await _crown(db_factory, topic, seat, turn_s, nonce_s, "e1", 1)

    # The seat's current session is sess-new; the entry drains from sess-old.
    async with db_factory() as session:
        await AgentSessionService(session).remember(
            conversation_id=topic,
            agent_handle=seat,
            resume_token="sess-new",
            harness="pi",
        )
        await session.commit()

    hook_work: dict = {}
    runner = _Runner()
    stale = _event(
        f"旧源 {nonce_t}", entry_id="e2", pos=2, session_id="sess-old", harness="pi"
    )
    await _bind_user_entry(
        db_factory, hook_work, runner, topic_id=topic, seat=seat, event=stale
    )

    from sqlalchemy import select

    from app.domain.agent.turn_inputs import AgentTurnInput as _Input

    async with db_factory() as session:
        row = await session.scalar(select(_Input).where(_Input.nonce == nonce_t))
    assert row.state != "bound", "旧 session 的 drain 不绑定"
    s_row = await turn_row(db_factory, turn_s)
    assert s_row.stopped_at is None, "旧 session 的 drain 不退休"
    assert runner.closed == []

    # The same entry from the CURRENT session binds and takes over.
    fresh = _event(
        f"新源 {nonce_t}", entry_id="e2", pos=2, session_id="sess-new", harness="pi"
    )
    await _bind_user_entry(
        db_factory, hook_work, runner, topic_id=topic, seat=seat, event=fresh
    )
    s_row = await turn_row(db_factory, turn_s)
    assert s_row.stopped_at is not None


@pytest.mark.anyio
async def test_a_pointer_writer_and_a_bind_serialize_on_the_row(db_factory):
    """The pointer writer commits a NEW session token while a bind waits on
    the row lock: the bind unblocks, reads the moved pointer, and refuses —
    nothing binds, nothing retires, marks stay."""
    import asyncio

    from sqlalchemy import select

    from app.domain.agent.turn_inputs import AgentTurnInput as _Input
    from app.domain.agent_session.models import AgentSession
    from app.domain.agent_session.services import AgentSessionService

    topic = await a_topic(db_factory)
    seat = "cheese"
    turn_s = await open_turn(db_factory, topic, delivered=True)
    turn_t = await open_turn(db_factory, topic, delivered=True)
    nonce_s = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    nonce_t = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    await _seed(db_factory, topic, turn_s, nonce_s)
    await _seed(db_factory, topic, turn_t, nonce_t)
    await _crown(db_factory, topic, seat, turn_s, nonce_s, "e1", 1)

    async with db_factory() as session:
        await AgentSessionService(session).remember(
            conversation_id=topic,
            agent_handle=seat,
            resume_token="sess-old",
            harness="pi",
        )
        await session.commit()

    writer = await db_factory().__aenter__()
    try:
        # The writer locks the pointer row and holds it, its new token not
        # yet committed.
        await writer.execute(
            select(AgentSession.id)
            .where(
                AgentSession.conversation_id == topic,
                AgentSession.agent_handle == seat,
                AgentSession.harness == "pi",
            )
            .with_for_update()
        )
        await AgentSessionService(writer).remember(
            conversation_id=topic,
            agent_handle=seat,
            resume_token="sess-new",
            harness="pi",
        )
        event = _event(
            f"接管 {nonce_t}", entry_id="e2", pos=2, session_id="sess-old", harness="pi"
        )
        hook_work: dict = {}
        runner = _Runner()
        attempted = asyncio.create_task(
            _bind_user_entry(
                db_factory, hook_work, runner, topic_id=topic, seat=seat, event=event
            )
        )
        await asyncio.sleep(0.5)  # let the bind block on the row lock
        await writer.commit()
        await asyncio.wait_for(attempted, timeout=30)

        async with db_factory() as session:
            row = await session.scalar(select(_Input).where(_Input.nonce == nonce_t))
        assert row.state != "bound", "pointer 已搬走：拒绝绑定"
        s_row = await turn_row(db_factory, turn_s)
        assert s_row.stopped_at is None, "pointer 已搬走：不退休"
        assert runner.closed == []
    finally:
        await writer.close()


@pytest.mark.anyio
async def test_a_replay_cleans_only_the_recorded_predecessor(db_factory):
    """A takeover committed but its cleanup was cancelled away: the replay
    drops exactly the retired predecessor the owner record names — another
    self-started seat's state, and anything still open, stays."""
    topic = await a_topic(db_factory)
    seat = "cheese"
    turn_s = await open_turn(db_factory, topic, delivered=True)
    turn_t = await open_turn(db_factory, topic, delivered=True)
    turn_u = await open_turn(db_factory, topic, delivered=True)
    nonce_s = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    nonce_t = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    await _seed(db_factory, topic, turn_s, nonce_s)
    await _seed(db_factory, topic, turn_t, nonce_t)
    await _crown(db_factory, topic, seat, turn_s, nonce_s, "e1", 1)

    # The takeover commits WITHOUT the cleanup (the caller was cancelled
    # between commit and cleanup).
    async with db_factory() as session:
        row = await bind(
            session,
            nonce=nonce_t,
            entry_id="e2",
            pos=2,
            generation=GENERATION,
            at=_NOW,
        )
        await transition(
            session,
            topic_id=topic,
            agent_handle=seat,
            session_id="sess-1",
            generation=GENERATION,
            work_id=turn_t,
            input_id=row.id,
            entry_id="e2",
            pos=2,
            at=_NOW,
        )
        await session.commit()

    hook_work = {
        (topic, turn_s): _state(topic, turn_s, self_started=True),
        (topic, turn_u): _state(topic, turn_u, self_started=True),
    }
    runner = _Runner()

    # The replay binds nothing (already bound) but must still clean — and
    # only the recorded predecessor.
    event = _event(f"接管 {nonce_t}", entry_id="e2", pos=2)
    await _bind_user_entry(
        db_factory, hook_work, runner, topic_id=topic, seat=seat, event=event
    )

    assert (topic, turn_s) not in hook_work, "记录里的前任被清"
    assert runner.closed == [turn_s]
    assert (topic, turn_u) in hook_work, "别的 self_started 一个不碰"
    assert runner.closed == [turn_s], "另一个座位/代次的 marks 不动"


@pytest.mark.anyio
async def test_an_event_from_a_detached_attachment_moves_nothing(db_factory):
    """A drain captured before a detach releases afterwards: its attachment
    is no longer the seat's current one — refused at the lock, and the
    ledger, owner, head, retirements and marks all stay exactly as they were
    (FB-56 attachment protocol)."""
    from sqlalchemy import select

    from app.domain.agent import attachments
    from app.domain.agent.turn_inputs import AgentTurnInput as _Input

    topic = await a_topic(db_factory)
    seat = "cheese"
    turn_s = await open_turn(db_factory, topic, delivered=True)
    turn_t = await open_turn(db_factory, topic, delivered=True)
    nonce_s = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    nonce_t = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    await _seed(db_factory, topic, turn_s, nonce_s)
    await _seed(db_factory, topic, turn_t, nonce_t)
    await _crown(db_factory, topic, seat, turn_s, nonce_s, "e1", 1)

    seat_tuple = (topic, seat)
    attachments.note(seat_tuple, None)  # detached: no live subscription

    event = AgentUserEntry(
        f"接管 {nonce_t}",
        entry_id="e2",
        pos=2,
        generation=GENERATION.hex,
        attachment="old-attachment-a1",
        eid="pi:user:e2",
    )
    hook_work = {(topic, turn_s): _state(topic, turn_s, self_started=True)}
    runner = _Runner()
    await _bind_user_entry(
        db_factory, hook_work, runner, topic_id=topic, seat=seat, event=event
    )

    async with db_factory() as session:
        row = await session.scalar(select(_Input).where(_Input.nonce == nonce_t))
    assert row.state != "bound", "detached 源：不绑定"
    s_row = await turn_row(db_factory, turn_s)
    assert s_row.stopped_at is None, "detached 源：不退休"
    assert (topic, turn_s) in hook_work and runner.closed == []

    # The same event from the seat's CURRENT attachment is accepted.
    attachments.note(seat_tuple, "old-attachment-a1")
    await _bind_user_entry(
        db_factory, hook_work, runner, topic_id=topic, seat=seat, event=event
    )
    s_row = await turn_row(db_factory, turn_s)
    t_row = await turn_row(db_factory, turn_t)
    assert s_row.stopped_at is not None, "当前源：正常接管"
    assert t_row.stopped_at is None
    assert (topic, turn_s) not in hook_work and runner.closed == [turn_s]
    attachments.note(seat_tuple, None)


@pytest.mark.anyio
async def test_a_reader_stalled_through_a_stop_and_reattach_moves_nothing(db_factory):
    """A reader drained an event before its bind, then the seat stopped and
    recovery re-attached: a DIFFERENT subscription instance on the SAME
    conversation — same session id, same journal generation, the pointer
    never moved. The session-id check cannot catch this one (the conversation
    really is the same); only the attachment guard can. The stalled callback
    is refused at the lock and moves nothing — ledger, owner, retirement,
    marks all stay — and the NEW source then binds and takes over normally
    (FB-56 stop/recover interleave)."""
    from sqlalchemy import select

    from app.domain.agent import attachments
    from app.domain.agent.turn_inputs import AgentTurnInput as _Input

    topic = await a_topic(db_factory)
    seat = "cheese"
    turn_s = await open_turn(db_factory, topic, delivered=True)
    turn_t = await open_turn(db_factory, topic, delivered=True)
    nonce_s = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    nonce_t = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    await _seed(db_factory, topic, turn_s, nonce_s)
    await _seed(db_factory, topic, turn_t, nonce_t)
    await _crown(db_factory, topic, seat, turn_s, nonce_s, "e1", 1)
    # The seat's pointer names this conversation — and keeps naming it
    # through the stop and the re-attach (same session, resumed journal).
    async with db_factory() as session:
        await AgentSessionService(session).remember(
            conversation_id=topic,
            agent_handle=seat,
            resume_token="sess-1",
            harness="pi",
        )
        await session.commit()

    seat_tuple = (topic, seat)
    attachments.note(seat_tuple, "attachment-a1")
    # Stop, then recovery re-attaches: a new subscription INSTANCE on the
    # same conversation. The old id is gone from the registry for good.
    attachments.note(seat_tuple, None)
    attachments.note(seat_tuple, "attachment-a2")

    stalled = AgentUserEntry(
        f"接管 {nonce_t}",
        entry_id="e2",
        pos=2,
        generation=GENERATION.hex,
        session_id="sess-1",
        harness="pi",
        attachment="attachment-a1",
        eid="pi:user:e2",
    )
    hook_work = {(topic, turn_s): _state(topic, turn_s, self_started=True)}
    runner = _Runner()
    await _bind_user_entry(
        db_factory, hook_work, runner, topic_id=topic, seat=seat, event=stalled
    )

    async with db_factory() as session:
        row = await session.scalar(select(_Input).where(_Input.nonce == nonce_t))
    assert row.state != "bound", "停顿在换代前的旧源：不绑定"
    s_row = await turn_row(db_factory, turn_s)
    assert s_row.stopped_at is None, "停顿在换代前的旧源：不退休"
    assert (topic, turn_s) in hook_work and runner.closed == []

    # The same entry from the seat's CURRENT attachment is accepted: the
    # pointer check passes (same conversation), the source is the live one.
    live = AgentUserEntry(
        f"接管 {nonce_t}",
        entry_id="e2",
        pos=2,
        generation=GENERATION.hex,
        session_id="sess-1",
        harness="pi",
        attachment="attachment-a2",
        eid="pi:user:e2",
    )
    await _bind_user_entry(
        db_factory, hook_work, runner, topic_id=topic, seat=seat, event=live
    )
    s_row = await turn_row(db_factory, turn_s)
    t_row = await turn_row(db_factory, turn_t)
    assert s_row.stopped_at is not None, "新源：正常接管"
    assert t_row.stopped_at is None
    assert (topic, turn_s) not in hook_work and runner.closed == [turn_s]
    attachments.note(seat_tuple, None)


@pytest.mark.anyio
async def test_a_cleanup_cancelled_after_commit_is_finished_by_the_replay(
    db_factory,
):
    """The takeover's commit landed, and the caller was cancelled between
    commit and cleanup (not a rollback): the memory side stays, and the
    replay — binding nothing new — finishes exactly the recorded
    predecessor's cleanup (FB-56 P2-2, real commit + cancel)."""
    import app.domain.agent.hook_stream as hs

    topic = await a_topic(db_factory)
    seat = "cheese"
    turn_s = await open_turn(db_factory, topic, delivered=True)
    turn_t = await open_turn(db_factory, topic, delivered=True)
    nonce_s = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    nonce_t = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    await _seed(db_factory, topic, turn_s, nonce_s)
    await _seed(db_factory, topic, turn_t, nonce_t)
    await _crown(db_factory, topic, seat, turn_s, nonce_s, "e1", 1)

    hook_work = {(topic, turn_s): _state(topic, turn_s, self_started=True)}
    runner = _Runner()
    event = _event(f"接管 {nonce_t}", entry_id="e2", pos=2)

    # The commit succeeds; the cleanup right after is cancelled.
    original = hs._drop_takeover_marks
    calls = {"n": 0}

    async def cancelling(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise asyncio.CancelledError()
        return await original(*args, **kwargs)

    hs._drop_takeover_marks = cancelling
    try:
        with pytest.raises(asyncio.CancelledError):
            await _bind_user_entry(
                db_factory, hook_work, runner, topic_id=topic, seat=seat, event=event
            )
    finally:
        hs._drop_takeover_marks = original

    s_row = await turn_row(db_factory, turn_s)
    assert s_row.stopped_at is not None, "DB 侧已提交（不是回滚）"
    assert (topic, turn_s) in hook_work, "取消在清理前：内存侧还在"
    assert runner.closed == []

    # The replay binds nothing (already bound) but finishes the cleanup —
    # the durable retired_turn_id is the whole predicate.
    await _bind_user_entry(
        db_factory, hook_work, runner, topic_id=topic, seat=seat, event=event
    )
    assert (topic, turn_s) not in hook_work, "重放补清准确前任"
    assert runner.closed == [turn_s]
