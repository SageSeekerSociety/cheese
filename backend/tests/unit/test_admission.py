"""Whose turn it is, across backend processes.

- A pool lets in at most its limit at once; the next waits until one leaves.
- A holder whose backend died (it stops renewing) loses its place once its
  lease runs out, and the next waiter gets it.
- Waiters are let in in the order they came, and each is told how many are
  ahead of it.
- A waiter that leaves the queue does not hold up those behind it.
- A turn entering again under its own ticket (taken up again after a restart)
  keeps the place it holds instead of taking a second one.
- A conversation answers one question at a time: one that will not wait is
  refused while another is being answered.
- A seat is one at a time across processes too: the second backend process
  waits for the first, and a seat held by a process that died comes back only
  once the lease runs out.
- Without Valkey a seat is serialized inside the process instead, which is what
  admission did before a seat was a lease.

Against a real Redis, through two clients: the holds are what other backend
processes see.
"""

import asyncio
import time
import uuid

import pytest
from redis.asyncio import Redis

from app.core.config import settings
from app.domain.agent import admission
from app.domain.agent.admission import Hold, Lease, Pool, enter


@pytest.fixture
async def clients(monkeypatch):
    monkeypatch.setattr(admission, "POLL_S", 0.05)
    one, two = Redis.from_url(settings.redis_url), Redis.from_url(settings.redis_url)
    yield one, two
    await one.aclose()
    await two.aclose()


def _pool(limit: int = 1) -> Pool:
    return Pool(f"test:{uuid.uuid4()}", limit)


@pytest.mark.anyio
async def test_a_second_process_waits_until_the_first_leaves(clients):
    one, two = clients
    pool = _pool()
    first = await enter(one, "a", pool=pool)
    assert first is not None

    second = asyncio.create_task(enter(two, "b", pool=pool))
    await asyncio.sleep(0.3)
    assert not second.done()

    await first.release()
    slot = await asyncio.wait_for(second, 2)
    assert slot is not None
    await slot.release()


@pytest.mark.anyio
async def test_a_place_held_by_a_dead_backend_lapses(clients, monkeypatch):
    one, two = clients
    monkeypatch.setattr(admission, "LEASE_S", 0.5)
    monkeypatch.setattr(admission, "RENEW_S", 3600.0)  # the holder never renews
    pool = _pool()
    assert await enter(one, "dead", pool=pool) is not None

    slot = await asyncio.wait_for(enter(two, "next", pool=pool), 3)

    assert slot is not None
    await slot.release()


@pytest.mark.anyio
async def test_waiters_are_let_in_in_the_order_they_came(clients):
    one, two = clients
    pool = _pool()
    holder = await enter(one, "holder", pool=pool)
    assert holder is not None
    told: dict[str, int] = {}
    admitted: list[str] = []

    async def wait(redis: Redis, ticket: str) -> None:
        async def queued(ahead: int) -> None:
            told[ticket] = ahead

        slot = await enter(redis, ticket, pool=pool, on_queued=queued)
        assert slot is not None
        admitted.append(ticket)
        await asyncio.sleep(0.1)
        await slot.release()

    waiters = []
    for index, ticket in enumerate(["w0", "w1", "w2"]):
        waiters.append(asyncio.create_task(wait((one, two)[index % 2], ticket)))
        await asyncio.sleep(0.15)
    await holder.release()
    await asyncio.wait_for(asyncio.gather(*waiters), 5)

    assert admitted == ["w0", "w1", "w2"]
    assert told == {"w0": 0, "w1": 1, "w2": 2}


@pytest.mark.anyio
async def test_a_waiter_that_leaves_does_not_hold_up_the_next(clients):
    one, two = clients
    pool = _pool()
    holder = await enter(one, "holder", pool=pool)
    assert holder is not None
    leaving = asyncio.create_task(enter(one, "leaving", pool=pool))
    await asyncio.sleep(0.15)
    behind = asyncio.create_task(enter(two, "behind", pool=pool))
    await asyncio.sleep(0.15)

    leaving.cancel()
    await asyncio.gather(leaving, return_exceptions=True)
    await holder.release()

    slot = await asyncio.wait_for(behind, 1)
    assert slot is not None
    await slot.release()


@pytest.mark.anyio
async def test_a_turn_taken_up_again_keeps_its_place(clients):
    one, two = clients
    pool = _pool()
    before = await enter(one, "turn", pool=pool)
    assert before is not None

    again = await asyncio.wait_for(enter(two, "turn", pool=pool), 1)
    other = asyncio.create_task(enter(two, "other", pool=pool))
    await asyncio.sleep(0.3)

    assert again is not None
    assert not other.done()
    await again.release()
    slot = await asyncio.wait_for(other, 2)
    assert slot is not None
    await slot.release()


@pytest.mark.anyio
async def test_a_conversation_answers_one_question_at_a_time(clients):
    one, two = clients
    hold = Hold(f"test-hold:{uuid.uuid4()}")
    first = await enter(one, str(uuid.uuid4()), hold=hold, wait_s=0)
    assert first is not None

    assert await enter(two, str(uuid.uuid4()), hold=hold, wait_s=0) is None

    await first.release()
    again = await enter(two, str(uuid.uuid4()), hold=hold, wait_s=0)
    assert again is not None
    await again.release()


@pytest.mark.anyio
async def test_a_seat_is_one_at_a_time_across_processes(clients, monkeypatch):
    one, two = clients
    monkeypatch.setattr(admission, "POLL_S", 0.05)
    lease = Lease(f"test-seat:{uuid.uuid4()}")
    inside: list[str] = []

    async def hold(redis: Redis, tag: str, seconds: float) -> None:
        monkeypatch.setattr(admission, "get_redis_client", lambda: redis)
        async with lease:
            inside.append(f"{tag}-in")
            await asyncio.sleep(seconds)
            inside.append(f"{tag}-out")

    first = asyncio.create_task(hold(one, "first", 0.5))
    await asyncio.sleep(0.2)
    second = asyncio.create_task(hold(two, "second", 0))

    await asyncio.gather(first, second)

    # The second process waited for the first: it entered after the first had
    # left, not beside it. As an asyncio.Lock this passed for both processes at
    # once, which is the bug this replaces.
    assert inside == ["first-in", "first-out", "second-in", "second-out"]


@pytest.mark.anyio
async def test_a_seat_lapses_when_the_process_holding_it_dies(clients, monkeypatch):
    one, two = clients
    monkeypatch.setattr(admission, "POLL_S", 0.05)
    monkeypatch.setattr(admission, "LEASE_S", 0.5)
    monkeypatch.setattr(admission, "RENEW_S", 3600.0)  # the holder never renews
    lease = Lease(f"test-seat:{uuid.uuid4()}")
    # A process killed outright: its hold is left there and nothing renews it.
    dead = await enter(one, str(uuid.uuid4()), hold=Hold(lease.key))

    monkeypatch.setattr(admission, "get_redis_client", lambda: two)
    started = time.monotonic()
    async with lease:
        pass
    took = time.monotonic() - started

    # It is waited out for the whole lease, not let go of: the one thing a
    # lease does worse than an asyncio.Lock, and only on the crash path.
    assert took >= 0.3
    assert took < 3
    assert dead is not None


@pytest.mark.anyio
async def test_a_seat_lets_go_at_once_when_the_turn_is_cancelled(clients, monkeypatch):
    one, two = clients
    monkeypatch.setattr(admission, "POLL_S", 0.05)
    monkeypatch.setattr(admission, "LEASE_S", 30.0)
    lease = Lease(f"test-seat:{uuid.uuid4()}")
    inside = asyncio.Event()

    async def hold() -> None:
        monkeypatch.setattr(admission, "get_redis_client", lambda: one)
        async with lease:
            inside.set()
            await asyncio.sleep(30)

    running = asyncio.create_task(hold())
    await inside.wait()
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)

    monkeypatch.setattr(admission, "get_redis_client", lambda: two)
    started = time.monotonic()
    async with lease:
        pass

    # A cancelled turn is not a dead process: it gives the seat up as it goes,
    # rather than leaving the next turn to wait out the lease.
    assert time.monotonic() - started < 1.0


@pytest.mark.anyio
async def test_without_valkey_a_seat_is_held_in_the_process(monkeypatch):
    monkeypatch.setattr(admission, "get_redis_client", lambda: None)
    lease = Lease(f"test-seat:{uuid.uuid4()}")
    inside: list[str] = []

    async def hold(tag: str, seconds: float) -> None:
        async with lease:
            inside.append(f"{tag}-in")
            await asyncio.sleep(seconds)
            inside.append(f"{tag}-out")

    assert not lease.locked()
    first = asyncio.create_task(hold("first", 0.3))
    await asyncio.sleep(0.1)
    assert lease.locked()
    second = asyncio.create_task(hold("second", 0))

    await asyncio.gather(first, second)

    assert inside == ["first-in", "first-out", "second-in", "second-out"]
    assert not lease.locked()


@pytest.mark.anyio
async def test_a_seat_falls_back_to_the_process_when_valkey_is_unreachable(monkeypatch):
    class Unreachable(Redis):
        async def set(self, *args, **kwargs):  # noqa: A003 — the client's own name
            raise ConnectionError("valkey is down")

    monkeypatch.setattr(admission, "get_redis_client", lambda: Unreachable())
    lease = Lease(f"test-seat:{uuid.uuid4()}")

    async with lease:
        assert lease.locked()

    assert not lease.locked()
