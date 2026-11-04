"""What an api pool process publishes reaches the realtime process's room
sockets (`realtime/relay.py`), and what it cannot know it refuses to make up.

The relay runs through the test Valkey (`REDIS_URL`), as on a deployment."""

import asyncio
import uuid

import pytest
from redis.asyncio import from_url

from app.core.config import settings
from app.core.process_role import ProcessRoleError
from app.domain.agent.realtime import relay
from app.domain.agent.realtime.broker import InProcessBroker, get_broker
from app.domain.agent.runtime import AgentWorkRunner


@pytest.fixture
async def valkey(monkeypatch):
    client = from_url(settings.redis_url)
    # A channel of this test's own, so a relay another test left running hears
    # nothing of it.
    monkeypatch.setattr(relay, "RELAY_CHANNEL", f"test-relay-{uuid.uuid4().hex}")
    monkeypatch.setattr(relay, "get_redis_client", lambda: client)
    yield client
    await client.aclose()


async def _subscribed(client, channel: str) -> None:
    for _ in range(100):
        counts = dict(await client.pubsub_numsub(channel))
        if counts.get(channel.encode(), 0):
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the realtime process never listened")


async def test_a_frame_published_in_the_pool_reaches_a_room_socket(valkey):
    broker = InProcessBroker()
    listening = asyncio.create_task(relay.relay_into(broker))
    try:
        await _subscribed(valkey, relay.RELAY_CHANNEL)
        async with broker.subscribe("room-1") as socket:
            frame = {"type": "block_updated", "block": {"id": "b1", "content": "hi"}}
            await relay.RelayBroker().publish("room-1", frame)
            got = await asyncio.wait_for(socket.get(), 5)
        assert got == frame
    finally:
        listening.cancel()
        await asyncio.gather(listening, return_exceptions=True)


async def test_typing_in_the_pool_is_typing_in_the_room(valkey):
    broker = InProcessBroker()
    listening = asyncio.create_task(relay.relay_into(broker))
    try:
        await _subscribed(valkey, relay.RELAY_CHANNEL)
        async with broker.subscribe("room-1") as socket:
            await relay.RelayBroker().typing("room-1", "alice", active=True)
            got = await asyncio.wait_for(socket.get(), 5)
        assert got["type"] != "error"
        assert "alice" in str(got)
    finally:
        listening.cancel()
        await asyncio.gather(listening, return_exceptions=True)


def test_the_pool_has_no_realtime_state_to_answer_from():
    pool = relay.RelayBroker()
    for ask in (
        lambda: pool.in_flight("room-1"),
        lambda: pool.active_turn_ids("room-1"),
        lambda: pool.activity,
        lambda: pool.subscribe("room-1"),
        lambda: pool.books_read(),
    ):
        with pytest.raises(ProcessRoleError):
            ask()
    # Whether someone listens is the realtime process's to know: the pool
    # publishes rather than drop a frame somebody reads.
    assert pool.has_subscribers("room-1")
    assert pool.any_subscribed("project:")


def test_a_pool_process_gets_the_relay_and_the_realtime_one_its_broker(monkeypatch):
    # The function behind the process-wide cache: clearing the cache would
    # hand later tests a broker without the listeners registered at import.
    make = get_broker.__wrapped__
    monkeypatch.setattr(settings, "backend_role", "api")
    assert isinstance(make(), relay.RelayBroker)
    monkeypatch.setattr(settings, "backend_role", "realtime")
    assert isinstance(make(), InProcessBroker)


async def test_a_pool_process_refuses_to_start_a_turn(monkeypatch):
    """It would otherwise wait forever for an owner lock it never takes."""
    runner = AgentWorkRunner(InProcessBroker())
    monkeypatch.setattr(settings, "backend_role", "api")
    with pytest.raises(ProcessRoleError):
        await runner.receive_message(None, uuid.uuid4(), author="alice", content="hi")
    with pytest.raises(ProcessRoleError):
        runner.submit(None, uuid.uuid4(), author="alice", content="hi", addressed=None)
