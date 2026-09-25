import asyncio
import uuid
import weakref
from types import SimpleNamespace

from app.domain.agent.harness import CLAUDE_CODE, Opening, SessionRef
from tests import conftest


async def _session_that_said_something(channel: conftest.StubChannel):
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese", harness=CLAUDE_CODE)
    handle = await channel.ensure(session, Opening(system_prompt=""))
    await channel.runtime._attach(handle)
    channel.starts(session.topic_id)
    return session.topic_id


async def test_pending_records_are_the_ones_the_room_has_not_landed(monkeypatch):
    monkeypatch.setattr(conftest, "_CHANNELS", weakref.WeakSet())
    channel = conftest.StubChannel()
    quiet = conftest.StubChannel()
    topic = await _session_that_said_something(channel)
    idle = await _session_that_said_something(quiet)
    await quiet.runtime.subscriptions[idle].drain()
    try:
        assert conftest._topics_with_pending_records() == {str(topic)}

        await channel.runtime.subscriptions[topic].drain()
        assert conftest._topics_with_pending_records() == set()
    finally:
        await channel.runtime._detach(topic)
        await quiet.runtime._detach(idle)


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
    monkeypatch.setattr(conftest, "get_work_runner", lambda: runner)
    sleeps = []

    def the_reader_lands_it(seconds):
        sleeps.append(seconds)
        asyncio.run(channel.runtime.subscriptions[topic].drain())

    monkeypatch.setattr(conftest.time, "sleep", the_reader_lands_it)

    conftest.wait_work_idle()

    assert sleeps == [0.01]
