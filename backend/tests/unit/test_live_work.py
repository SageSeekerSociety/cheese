"""``LiveWork``：这台进程此刻手上正接着的活，按房间、按轮次键住。

This is exactly the state that used to sit scattered over ``ChatService``
(``hook_work`` / ``active_turn_ids`` / the seat and topic locks / the note
tables). What is worth pinning is not the storage but the keys and what moves
together: one seat's turns queue on one lock while two seats in a room run
side by side; two live turns in one room are both remembered and each ends on
its own; and two services never see each other's work. These hold the real
object — the smallest layer that can be held, no database and no socket.

``active_turn_ids`` is a cache of the open ``AgentTurn`` rows now, so the marks
are async and write through: these pin that ``mark_turn_active`` hands the
durable half the SAME two keys it keys the cache with, that the mark lands only
when the durable half says the row is there (a confirm that raises or says no
leaves the cache clear — no phantom running turn), and that dropping a mark never
closes a row (the authority's own lifecycle does).
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from app.api import deps as session_turn_deps
from app.domain.agent.chat import ChatService
from app.domain.agent.turn.state.live import HookWorkState, LiveWork
from tests.conftest import stub_compute


def _service() -> ChatService:
    return ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=None,
        base_system_prompt="You are Cheese.",
        workspace_root="/tmp/live-work-ws",
        compute=stub_compute(),
    )


def test_one_seat_shares_a_lock_and_two_seats_do_not():
    live = LiveWork()
    room, other = uuid.uuid4(), uuid.uuid4()
    # The same (room, seat) is the same lock, so that agent's turns queue…
    assert live.seat_lock_for(room, "cheese") is live.seat_lock_for(room, "cheese")
    # …while another seat in the room, or the same seat in another room, is
    # a different lock and runs beside it.
    assert live.seat_lock_for(room, "cheese") is not live.seat_lock_for(room, "other")
    assert live.seat_lock_for(room, "cheese") is not live.seat_lock_for(other, "cheese")


def test_the_room_lock_is_its_own_key_space():
    live = LiveWork()
    room = uuid.uuid4()
    assert live.lock_for(room) is live.lock_for(room)
    assert live.lock_for(room) is not live.lock_for(uuid.uuid4())
    # A room lock and a seat lock for the same room are different locks.
    assert live.lock_for(room) is not live.seat_lock_for(room, "cheese")


def _state(topic_id, work_id, **changes) -> HookWorkState:
    fields = dict(
        project_id=uuid.uuid4(),
        topic_id=topic_id,
        work_id=work_id,
        pending_ids=set(),
        reply_to=None,
        roster=[],
        topic_refs=[],
        continuation_id=None,
        route="native",
        acting_agent="cheese",
        agent_pool=None,
        user_text="",
        started_at=datetime.now(UTC),
        agent_instance_handle="inst",
    )
    fields.update(changes)
    return HookWorkState(**fields)


@pytest.mark.anyio
async def test_mark_turn_active_writes_through_with_the_same_two_keys():
    """The cache and the durable half are keyed by the same ``(topic, work)``.

    A write-through that invented its own key would leave the authority naming
    a turn the cache never holds (or the other way round), and every later read
    would disagree with the row it is supposed to mirror.
    """
    calls: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def write_open(topic_id, work_id):
        calls.append((topic_id, work_id))
        return True

    live = LiveWork(write_open=write_open)
    topic, work = uuid.uuid4(), uuid.uuid4()
    assert await live.mark_turn_active(topic, work) is True
    assert live.active_turn_ids[topic] == {work}
    assert calls == [(topic, work)]


@pytest.mark.anyio
async def test_activate_cached_marks_on_the_callers_word_and_reads_nothing():
    """The post-commit mark does no read at all.

    A caller that waited on its own commit already knows the row is there; a
    fresh connection re-reading it can answer no at the commit boundary, and a
    no would drop a mark that belongs on. So the mark rests on the caller's
    awaited commit, and this asks the database nothing — it must not be able to
    reach a write-through that could drop the mark.
    """
    calls: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def write_open(topic_id, work_id):
        calls.append((topic_id, work_id))
        return False  # would DROP the mark if this path consulted it

    live = LiveWork(write_open=write_open)
    topic, work = uuid.uuid4(), uuid.uuid4()
    live.activate_cached(topic, work)
    assert live.active_turn_ids[topic] == {work}
    assert calls == []


@pytest.mark.anyio
async def test_a_failed_confirm_leaves_no_cache_entry():
    """A confirm that raises must not leave the turn marked active.

    The mark is only worth anything because the authority backs it: if the
    write-through blows up, a cache entry left behind is a turn every reader
    takes for 「在跑」 while no row exists — a phantom running turn that only a
    restart clears. The confirm therefore runs BEFORE the cache moves, so its
    failure leaves nothing.
    """

    async def write_open(topic_id, work_id):
        raise RuntimeError("db gone")

    live = LiveWork(write_open=write_open)
    topic, work = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(RuntimeError):
        await live.mark_turn_active(topic, work)
    assert topic not in live.active_turn_ids


@pytest.mark.anyio
async def test_a_confirm_that_says_no_does_not_mark_and_clears_a_stale_mark():
    """The authority's own word decides: no open row, no mark.

    ``confirm_open`` answers False for a turn whose row the lifecycle has not
    committed yet and for one a sweep closed — both are turns the cache must
    not name. A stale entry already under the key goes too: a closed or absent
    row must not be revived by the cache.
    """
    open_now = uuid.uuid4()

    async def write_open(topic_id, work_id):
        return work_id == open_now

    live = LiveWork(write_open=write_open)
    topic, work = uuid.uuid4(), uuid.uuid4()
    # A stale entry under the same key is dropped, not kept alive.
    live.active_turn_ids[topic] = {work}
    assert await live.mark_turn_active(topic, work) is False
    assert topic not in live.active_turn_ids
    # And a row that IS open still marks.
    assert await live.mark_turn_active(topic, open_now) is True
    assert live.active_turn_ids[topic] == {open_now}


@pytest.mark.anyio
async def test_mark_turn_inactive_never_closes_a_row():
    """Letting go of the cache must not end a real turn.

    Closing the interval belongs to the turn's own lifecycle; a cache that
    closed it on the way out would be ending a turn the authority still has
    open (the design forbids it: no using the cache to undo a real turn).
    """
    writes: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def write_open(topic_id, work_id):
        writes.append((topic_id, work_id))
        return True

    live = LiveWork(write_open=write_open)
    topic, work = uuid.uuid4(), uuid.uuid4()
    await live.mark_turn_active(topic, work)
    await live.mark_turn_inactive(topic, work)
    # Dropping the mark only dropped the cache key: the sole durable write is
    # still the one open, and nothing closed the interval behind the turn.
    assert writes == [(topic, work)]
    assert topic not in live.active_turn_ids


@pytest.mark.anyio
async def test_a_confirm_cancelled_mid_await_still_lets_the_mark_go():
    """Cancellation during confirmation propagates but still evicts the mark."""
    entered = asyncio.Event()

    async def confirm_closed(topic_id, work_id):
        entered.set()
        await asyncio.Event().wait()

    live = LiveWork(confirm_closed=confirm_closed)
    topic, work = uuid.uuid4(), uuid.uuid4()
    await live.mark_turn_active(topic, work)
    assert live.active_turn_ids[topic] == {work}

    letting_go = asyncio.create_task(live.mark_turn_inactive(topic, work))
    await asyncio.wait_for(entered.wait(), timeout=5)
    letting_go.cancel()
    with pytest.raises(asyncio.CancelledError):
        await letting_go

    assert topic not in live.active_turn_ids


@pytest.mark.anyio
async def test_two_live_turns_are_both_remembered_and_close_independently():
    live = LiveWork()
    topic = uuid.uuid4()
    a, b = uuid.uuid4(), uuid.uuid4()
    await live.mark_turn_active(topic, a)
    await live.mark_turn_active(topic, b)
    assert live.active_turn_ids[topic] == {a, b}

    # Closing one turn leaves the other live — no room-wide clear.
    await live.mark_turn_inactive(topic, a)
    assert live.active_turn_ids[topic] == {b}
    await live.mark_turn_inactive(topic, b)
    assert topic not in live.active_turn_ids  # the last one takes the room out


@pytest.mark.anyio
async def test_consuming_work_id_does_not_misattribute_between_live_turns():
    live = LiveWork()
    topic = uuid.uuid4()
    a, b = uuid.uuid4(), uuid.uuid4()
    live.hook_work[(topic, a)] = _state(topic, a, agent_instance_handle="inst-a")
    live.hook_work[(topic, b)] = _state(topic, b, agent_instance_handle="inst-b")
    await live.mark_turn_active(topic, a)
    await live.mark_turn_active(topic, b)

    to = lambda handle: lambda s: s.agent_instance_handle == handle  # noqa: E731
    # Exactly one recipient matches: that turn is the one.
    assert live.consuming_work_id(topic, to("inst-a")) == a
    assert live.consuming_work_id(topic, to("inst-b")) == b
    # Two live turns and no exact match: held, never sent to a guessed turn.
    assert live.consuming_work_id(topic, lambda s: True) is None
    assert live.consuming_work_id(topic) is None

    # Closing one leaves the survivor the unambiguous target.
    await live.mark_turn_inactive(topic, a)
    assert live.consuming_work_id(topic) == b


@pytest.mark.anyio
async def test_the_prompt_lock_marks_nothing_before_the_row_is_committed():
    """The lock reserves the seat; it does not claim a turn is running.

    The turn's ``AgentTurn`` row is opened INSIDE the lock (``open_turn`` /
    ``open_interval``), so a mark taken when the lock is entered names a turn
    the authority has never seen — a phantom running turn for every reader,
    cleared only by a restart. No database here, where a real confirm would say
    yes: anything marked is therefore the lock's own doing.
    """
    chat = _service()
    topic, work = uuid.uuid4(), uuid.uuid4()
    async with chat._prompt_lock(topic, work, "cheese"):
        assert work not in chat.live.active_turn_ids.get(topic, set())
    assert not chat.live.active_turn_ids.get(topic)


@pytest.mark.anyio
async def test_two_services_do_not_share_the_work_they_are_holding():
    one, two = _service(), _service()
    assert one.live is not two.live
    topic = uuid.uuid4()
    await one.live.mark_turn_active(topic, uuid.uuid4())
    assert one.live.active_turn_ids
    assert two.live.active_turn_ids == {}
