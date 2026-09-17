"""A background task the caller holds still reports its own crash.

The shape this replaces — `registry.add(task)` then
`task.add_done_callback(registry.discard)` — retires the task without ever
reading its exception, so a crash surfaces (if ever) as a GC-time warning
attached to nothing, long after the work went missing.
"""

import asyncio
import logging

import pytest

from app.core.background import hold


@pytest.mark.anyio
async def test_a_crash_is_logged_with_the_name_the_caller_gave(caplog) -> None:
    async def boom() -> None:
        raise RuntimeError("the room was never told")

    registry: set[asyncio.Task] = set()
    with caplog.at_level(logging.ERROR, logger="cheesex.background"):
        task = hold(asyncio.create_task(boom()), registry, name="accept-notice-42")
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    assert not registry, "the finished task was not dropped from the registry"
    records = [r for r in caplog.records if r.name == "cheesex.background"]
    assert records, "a crash in held work produced no log record"
    assert records[0].levelno == logging.ERROR
    assert "accept-notice-42" in records[0].getMessage()
    assert "the room was never told" in caplog.text


@pytest.mark.anyio
async def test_a_cancelled_task_is_not_a_failure(caplog) -> None:
    async def forever() -> None:
        await asyncio.sleep(3600)

    registry: set[asyncio.Task] = set()
    with caplog.at_level(logging.ERROR, logger="cheesex.background"):
        task = hold(asyncio.create_task(forever()), registry, name="shutdown-me")
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    assert not registry
    assert not [r for r in caplog.records if r.name == "cheesex.background"], (
        "shutdown cancellation was reported as a failure"
    )


@pytest.mark.anyio
async def test_success_is_quiet_and_the_registry_empties(caplog) -> None:
    async def fine() -> None:
        return None

    registry: set[asyncio.Task] = set()
    with caplog.at_level(logging.ERROR, logger="cheesex.background"):
        task = hold(asyncio.create_task(fine()), registry, name="quiet")
        assert registry == {task}, "the task was not held while it ran"
        await task
        await asyncio.sleep(0)

    assert not registry
    assert not [r for r in caplog.records if r.name == "cheesex.background"]
