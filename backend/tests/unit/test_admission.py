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
- A take that cannot reach Valkey waits for the turn inside all the same, and
  leaves no key behind when it breaks on the way; and the seat comes free when
  the task that closes a turn is not the one that took it.
- A seat this process is only queued for reads as free here, not as taken: a
  caller that refuses a held seat must not refuse it for one it is waiting on.

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
    key = f"test-seat:{uuid.uuid4()}"
    inside: list[str] = []

    async def hold(redis: Redis, tag: str, seconds: float) -> None:
        # Its own Lease, as a second process has: one shared object would queue
        # the two tasks on its own asyncio.Lock and pass without Valkey, which
        # is the bug this replaces.
        lease = Lease(key)
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


@pytest.mark.anyio
async def test_a_seat_does_not_open_for_a_take_that_cannot_reach_valkey(
    clients, monkeypatch
):
    """The half that reached Valkey and the half that fell back are the same
    seat here. A take that cannot reach Valkey is one this process holds the
    lock for, so it waits for the turn inside rather than running beside it —
    the turn inside is one Valkey never saw, and the other process's turn is
    the one it would have run into."""
    one, _ = clients
    key = f"test-seat:{uuid.uuid4()}"
    lease = Lease(key)
    inside: list[str] = []

    class Unreachable(Redis):
        async def set(self, *args, **kwargs):  # noqa: A003 — the client's own name
            raise ConnectionError("valkey is down")

        async def eval(self, *args, **kwargs):  # noqa: A003 — the client's own name
            raise ConnectionError("valkey is down")

    offline = Unreachable()

    def client_for_this_task() -> Redis:
        return offline if asyncio.current_task().get_name() == "offline" else one

    monkeypatch.setattr(admission, "get_redis_client", client_for_this_task)

    async def hold(tag: str, seconds: float) -> None:
        async with lease:
            inside.append(f"{tag}-in")
            await asyncio.sleep(seconds)
            inside.append(f"{tag}-out")

    first = asyncio.create_task(hold("valkey", 0.3), name="valkey")
    await asyncio.sleep(0.1)
    second = asyncio.create_task(hold("offline", 0), name="offline")
    await asyncio.gather(first, second)

    assert inside == ["valkey-in", "valkey-out", "offline-in", "offline-out"]


@pytest.mark.anyio
async def test_a_seat_a_broken_take_wrote_is_not_left_held_in_valkey(
    clients, monkeypatch
):
    """A take writes the hold and then renews it. A Valkey that goes between
    the two leaves the key behind while this process, which never got the
    seat, runs on — and every process reads the seat as taken for ``LEASE_S``
    for nothing."""
    one, _ = clients
    key = f"test-seat:{uuid.uuid4()}"
    lease = Lease(key)
    real_eval = one.eval
    calls = 0

    async def flaky_eval(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:  # the renewal inside `enter`, right after the write
            raise ConnectionError("valkey went down mid-take")
        return await real_eval(*args, **kwargs)

    monkeypatch.setattr(one, "eval", flaky_eval)
    monkeypatch.setattr(admission, "get_redis_client", lambda: one)

    async with lease:
        assert lease.locked()

    assert calls > 1, "the take never got as far as dropping what it wrote"
    assert await one.get(key) is None


@pytest.mark.anyio
async def test_a_seat_is_let_go_by_the_task_that_closes_the_turn(clients, monkeypatch):
    """A frame abandoned by a timeout is closed by the loop in a task of its
    own, not the one that took the seat. The seat comes free either way: the
    lock lets go for whoever gets there, and the hold goes with it. Keyed to
    the task that took it, the release was skipped and the seat stayed taken
    for as long as the process lived."""
    one, _ = clients
    key = f"test-seat:{uuid.uuid4()}"
    lease = Lease(key)
    monkeypatch.setattr(admission, "get_redis_client", lambda: one)

    async def frames():
        async with lease:
            yield "held"

    frames_taken = frames()
    assert await frames_taken.__anext__() == "held"
    assert lease.locked()
    assert await one.get(key) is not None

    async def close_the_frame() -> None:
        assert asyncio.current_task().get_name() == "closer"
        await frames_taken.aclose()

    await asyncio.create_task(close_the_frame(), name="closer")

    assert not lease.locked()
    assert await one.get(key) is None


@pytest.mark.anyio
async def test_a_seat_waited_for_here_does_not_read_as_taken_here(clients, monkeypatch):
    """``locked()`` answers for this process holding the seat, not for it
    queueing behind another process for one. Read from the process lock — which
    a take holds from the moment it starts waiting for the key — a caller that
    refuses a busy seat would refuse a second turn merely because the first was
    queued, and tell it to wait for a turn of its own that is not running."""
    one, two = clients
    key = f"test-seat:{uuid.uuid4()}"
    lease = Lease(key)
    monkeypatch.setattr(admission, "get_redis_client", lambda: one)
    inside: list[str] = []

    # The other process's hold, written as `enter` writes it.
    await two.set(key, "another-process", px=int(admission.LEASE_S * 1000))

    async def take() -> None:
        async with lease:
            inside.append("in")
            assert lease.locked()
            inside.append("out")

    waiting = asyncio.create_task(take())
    await asyncio.sleep(0.3)
    assert inside == [], "the take did not wait for the other process"
    assert not lease.locked(), "a seat only waited for is not held here"

    await two.delete(key)

    await asyncio.wait_for(waiting, timeout=10)
    assert inside == ["in", "out"]
    assert not lease.locked()
