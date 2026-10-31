"""A worker's kept CI board: opens that arrive while it is being built wait for
that one build instead of each starting their own."""

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
