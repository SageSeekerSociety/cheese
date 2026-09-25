import asyncio
import uuid
import weakref
from types import SimpleNamespace

import pytest

from app.domain.agent.harness import CLAUDE_CODE, Opening, SessionRef
from tests import conftest
from tests.conftest import close_topic_subscriptions, drain_hooks, settle_turn


@pytest.fixture(autouse=True)
def _only_this_tests_channels(monkeypatch):
    monkeypatch.setattr(conftest, "_CHANNELS", weakref.WeakSet())


class Room:
    """A service holding one stub session, the way a ChatService holds its pool."""

    def __init__(self) -> None:
        self.channel = conftest.StubChannel()
        self.runtime = self.channel.runtime
        self.service = SimpleNamespace(
            _compute=SimpleNamespace(_runtimes=lambda: [self.runtime]),
            _hook_work={},
        )

    async def open(self) -> uuid.UUID:
        session = SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese", harness=CLAUDE_CODE)
        handle = await self.channel.ensure(session, Opening(system_prompt=""))
        await self.runtime._attach(handle)
        self.channel.starts(session.topic_id)
        return session.topic_id


async def test_settle_turn_lands_what_the_session_said():
    room = Room()
    topic = await room.open()
    assert conftest._topics_with_pending_records() == {str(topic)}

    await settle_turn(room.service, topic)

    assert conftest._topics_with_pending_records() == set()
    await close_topic_subscriptions(room.service, topic)


async def test_settle_turn_waits_for_the_rooms_open_work():
    room = Room()
    topic = await room.open()
    room.service._hook_work[(topic, uuid.uuid4())] = object()
    settled = asyncio.create_task(settle_turn(room.service, topic))
    await asyncio.sleep(0.1)
    assert not settled.done()

    room.service._hook_work.clear()
    await asyncio.wait_for(settled, 5)
    await close_topic_subscriptions(room.service, topic)


async def test_settle_turn_does_not_wait_on_another_room():
    room = Room()
    topic = await room.open()
    other = await room.open()
    room.service._hook_work[(other, uuid.uuid4())] = object()

    await asyncio.wait_for(settle_turn(room.service, topic), 5)
    await close_topic_subscriptions(room.service, topic)
    await close_topic_subscriptions(room.service, other)


async def test_a_turn_that_never_closes_names_the_open_work():
    room = Room()
    topic = await room.open()
    room.service._hook_work[(topic, uuid.uuid4())] = object()

    with pytest.raises(AssertionError, match=f"turn on {topic} never closed"):
        await settle_turn(room.service, topic, tries=3)
    await close_topic_subscriptions(room.service, topic)


async def test_drain_hooks_lands_what_was_said_without_waiting_for_an_ending():
    room = Room()
    topic = await room.open()
    room.service._hook_work[(topic, uuid.uuid4())] = object()

    await drain_hooks(room.channel, topic)

    assert conftest._topics_with_pending_records() == set()
    await close_topic_subscriptions(room.service, topic)


async def test_close_topic_subscriptions_stops_the_reader():
    room = Room()
    topic = await room.open()

    await close_topic_subscriptions(room.service, topic)

    assert topic not in room.runtime.subscriptions
    assert not room.runtime.holds(topic)
