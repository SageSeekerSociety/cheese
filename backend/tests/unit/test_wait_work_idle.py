import asyncio
import uuid
import weakref
from types import SimpleNamespace

from app.api import deps
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from tests import conftest


async def _session_that_said_something(channel: conftest.StubChannel):
    """A seat whose session said something nobody has read yet."""
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese", harness=CLAUDE_CODE)
    await channel.runtime.ensure(session, system_prompt="")
    channel.starts(session.topic_id)
    return session.topic_id, "cheese"


async def _read(channel: conftest.StubChannel, seat) -> None:
    """Read the seat as the room does, until what it said has landed."""
    channel.runtime._listen(seat)
    await conftest.drain_hooks(channel, seat[0])


async def test_pending_records_are_the_ones_the_room_has_not_landed(monkeypatch):
    monkeypatch.setattr(conftest, "_CHANNELS", weakref.WeakSet())
    channel = conftest.StubChannel()
    quiet = conftest.StubChannel()
    topic = await _session_that_said_something(channel)
    idle = await _session_that_said_something(quiet)
    await _read(quiet, idle)
    try:
        assert conftest._topics_with_pending_records() == {str(topic[0])}

        await _read(channel, topic)
        assert conftest._topics_with_pending_records() == set()
    finally:
        await channel.runtime._detach(topic)
        await quiet.runtime._detach(idle)


async def test_a_channel_an_earlier_test_left_is_not_waited_on(monkeypatch):
    """Only this test's channels are asked.

    A channel outlives its test until the garbage collector reaches the cycle
    it sits in — in a loaded shard, many tests later. Its reading died with its
    own portal loop, so the records it left can never land; counting them made
    every later test pay the whole ceiling for work nobody could drain.
    """
    monkeypatch.setattr(conftest, "_CHANNELS", weakref.WeakSet())
    stale = conftest.StubChannel()
    stale_topic = await _session_that_said_something(stale)
    # What `_stub_channels_scoped_to_this_test` snapshots at the test's start.
    monkeypatch.setattr(conftest, "_CHANNELS_BEFORE_TEST", frozenset({stale}))
    sleeps = []
    monkeypatch.setattr(conftest.time, "sleep", sleeps.append)
    conftest.wait_work_idle(
        SimpleNamespace(
            _tasks=set(),
            _broker=SimpleNamespace(_active={}),
            active_work_count=lambda: 0,
        )
    )
    assert sleeps == []
    mine = conftest.StubChannel()
    my_topic = await _session_that_said_something(mine)
    try:
        assert conftest._topics_with_pending_records() == {str(my_topic[0])}

        await _read(mine, my_topic)
        assert conftest._topics_with_pending_records() == set()
    finally:
        await stale.runtime._detach(stale_topic)
        await mine.runtime._detach(my_topic)


def test_wait_work_idle_waits_for_records_but_not_an_idle_lifecycle(monkeypatch):
    """A lifecycle the broker still marks active is not work teardown can drain:
    once what the session said has landed, teardown returns at once."""
    monkeypatch.setattr(conftest, "_CHANNELS", weakref.WeakSet())
    channel = conftest.StubChannel()
    topic = asyncio.run(_session_that_said_something(channel))
    runner = SimpleNamespace(
        _tasks=set(),
        _broker=SimpleNamespace(_active={str(topic): {"turn"}}),
        active_work_count=lambda: 1,
    )
    monkeypatch.setattr(deps, "get_work_runner", lambda: runner)
    sleeps = []

    async def read_and_let_go() -> None:
        await _read(channel, topic)
        await channel.runtime._detach(topic)

    def the_reader_lands_it(seconds):
        sleeps.append(seconds)
        asyncio.run(read_and_let_go())

    monkeypatch.setattr(conftest.time, "sleep", the_reader_lands_it)
    conftest.wait_work_idle()

    assert sleeps == [0.01]
