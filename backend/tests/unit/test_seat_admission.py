"""Whose seat admission is whose.

A turn's first input is admitted under its seat and then opens the turn, which
is admitted under the same seat again: the inner one must not wait for the
outer one to let go. Another task must still wait.
"""

import asyncio

import pytest

from app.domain.agent import admission
from app.domain.agent.admission import Lease
from app.domain.agent.seat_admission import seat_admission


@pytest.fixture(autouse=True)
def without_valkey(monkeypatch):
    """The seat held in the process, so nothing here needs Redis: re-entrancy
    is admission's own, above whichever way the seat is held."""
    monkeypatch.setattr(admission, "get_redis_client", lambda: None)


def _seat() -> Lease:
    return Lease("test-seat:admission")


async def _nested(lease: Lease, seen: list[str]) -> None:
    async with seat_admission(lease):
        seen.append("outer")
        async with seat_admission(lease):
            seen.append("inner")


@pytest.mark.anyio
async def test_a_turn_takes_its_own_seat_again_without_waiting():
    lease = _seat()
    seen: list[str] = []

    await asyncio.wait_for(_nested(lease, seen), 2)

    assert seen == ["outer", "inner"]
    assert not lease.locked()


@pytest.mark.anyio
async def test_another_task_waits_for_the_seat():
    lease = _seat()
    inside: list[str] = []

    async def hold() -> None:
        async with seat_admission(lease):
            inside.append("holding")
            await asyncio.sleep(0.2)
            inside.append("done")

    first = asyncio.create_task(hold())
    await asyncio.sleep(0.05)
    assert lease.locked()
    second = asyncio.create_task(hold())
    await asyncio.sleep(0.05)

    assert inside == ["holding"]
    assert not second.done()

    await asyncio.wait_for(asyncio.gather(first, second), 2)
    assert inside == ["holding", "done", "holding", "done"]


@pytest.mark.anyio
async def test_a_plain_lock_is_still_admitted():
    """A double for "this seat is never really taken" hands over an
    ``asyncio.Lock``; admission takes it."""
    lock = asyncio.Lock()
    seen: list[str] = []

    async with seat_admission(lock):
        async with seat_admission(lock):
            seen.append("inner")
        assert lock.locked()

    assert seen == ["inner"]
    assert not lock.locked()
