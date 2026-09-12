"""Transport retries and lost readers must not create a second model input."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.agent.harness.codex.runner import Runner


@pytest.mark.anyio
async def test_cancelled_reader_and_duplicate_request_share_one_submission(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()

    async def send(text):
        entered.set()
        await release.wait()
        return "turn"

    runner = Runner(tmp_path, AsyncMock())
    sender = AsyncMock(side_effect=send)
    runner.session = SimpleNamespace(send=sender)
    first = asyncio.create_task(runner.send("input", "text"))
    try:
        await entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(runner.send("input", "text"))
        release.set()
        result = await second
        sender.assert_awaited_once_with("text")
    finally:
        release.set()
        await runner.close()
    reopened = Runner(tmp_path, AsyncMock())
    try:
        assert await reopened.send("input", "text") == result
        with pytest.raises(ValueError, match="different text"):
            await reopened.send("input", "different")
    finally:
        await reopened.close()


@pytest.mark.anyio
async def test_interrupted_submission_is_not_silently_sent_again(tmp_path):
    runner = Runner(tmp_path, AsyncMock())
    runner.journal.begin_input("uncertain", "text")
    await runner.close()
    reopened = Runner(tmp_path, AsyncMock())
    try:
        with pytest.raises(RuntimeError, match="unresolved"):
            await reopened.send("uncertain", "text")
        assert reopened.journal.input("uncertain")[1] == "sending"
    finally:
        await reopened.close()


@pytest.mark.anyio
async def test_event_reads_do_not_consume_and_survive_reopening(tmp_path):
    runner = Runner(tmp_path, AsyncMock())
    try:
        await runner.record({"method": "first"})
        await runner.record({"method": "second"})
        entries = (await runner.dispatch("events", {}))["events"]
        assert (await runner.dispatch("events", {}))["events"] == entries
    finally:
        await runner.close()
    reopened = Runner(tmp_path, AsyncMock())
    try:
        result = await reopened.dispatch("events", {"after": entries[0]["sequence"]})
        assert result["events"] == entries[1:]
    finally:
        await reopened.close()
