"""A worker's kept CI board: requests that arrive while it is being built wait
for that one build instead of each starting their own, and when a build fails
or is cancelled, one build replaces it, never two at once."""

import asyncio

import pytest
from cachetools import TTLCache

from app.domain.ci_activity import board


@pytest.fixture(autouse=True)
def _no_board_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(board, "_boards", TTLCache(maxsize=32, ttl=board.BOARD_TTL_S))
    monkeypatch.setattr(board, "_building", {})


@pytest.mark.anyio
async def test_opens_that_arrive_together_share_one_build():
    builds = 0
    release = asyncio.Event()

    async def build() -> dict:
        nonlocal builds
        builds += 1
        await release.wait()
        return {"built": builds}

    opens = [asyncio.ensure_future(board.kept_board("o/r", 7, build)) for _ in range(5)]
    await asyncio.sleep(0)
    release.set()

    boards = await asyncio.gather(*opens)

    assert builds == 1
    assert boards == [{"built": 1}] * 5


@pytest.mark.anyio
async def test_a_failed_build_is_not_kept_and_the_next_open_builds_again():
    attempts = 0

    async def build() -> dict:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("database went away")
        return {"built": attempts}

    with pytest.raises(RuntimeError):
        await board.kept_board("o/r", 7, build)

    assert await board.kept_board("o/r", 7, build) == {"built": 2}


class _Builds:
    """Builds of one board, each held until the test lets it finish: how many
    ran, and the most that ran at once."""

    def __init__(self) -> None:
        self.started = 0
        self.running = 0
        self.most_at_once = 0
        self.release = [asyncio.Event() for _ in range(3)]

    async def build(self) -> dict:
        n = self.started
        self.started += 1
        self.running += 1
        self.most_at_once = max(self.most_at_once, self.running)
        try:
            await self.release[n].wait()
            if n == 0:
                raise RuntimeError("database went away")
            return {"built": n}
        finally:
            self.running -= 1


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def _replaced(end_first) -> None:
    builds = _Builds()
    first = asyncio.ensure_future(board.kept_board("o/r", 7, builds.build))
    await _settle()
    second = asyncio.ensure_future(board.kept_board("o/r", 7, builds.build))
    await _settle()

    await end_first(first, builds)
    await _settle()
    third = asyncio.ensure_future(board.kept_board("o/r", 7, builds.build))
    await _settle()
    for event in builds.release:
        event.set()
    results = await asyncio.gather(first, second, third, return_exceptions=True)

    assert builds.most_at_once == 1
    assert results[1:] == [{"built": 1}, {"built": 1}]
    assert await board.kept_board("o/r", 7, builds.build) == {"built": 1}


@pytest.mark.anyio
async def test_a_failed_build_is_replaced_by_one_build_not_two():
    async def fail(first, builds):
        builds.release[0].set()

    await _replaced(fail)


@pytest.mark.anyio
async def test_a_cancelled_build_is_replaced_by_one_build_not_two():
    async def cancel(first, builds):
        first.cancel()

    await _replaced(cancel)
