"""Transport retries and lost readers must not create a second model input."""

import asyncio
import fcntl
import json
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
    runner.journal.begin_input(
        "uncertain", json.dumps({"text": "text", "images": []}, sort_keys=True)
    )
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


@pytest.mark.anyio
async def test_image_content_is_part_of_the_input_identity(tmp_path):
    runner = Runner(tmp_path, AsyncMock())
    sender = AsyncMock(return_value="turn")
    runner.session = SimpleNamespace(send=sender)
    try:
        await runner.send("input", "look", ["data:image/png;base64,YQ=="])
        with pytest.raises(ValueError):
            await runner.send("input", "look", ["data:image/png;base64,Yg=="])
        sender.assert_awaited_once_with("look", images=["data:image/png;base64,YQ=="])
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_protocol_failure_releases_session_lock_and_files(tmp_path):
    async def fail():
        raise ValueError("malformed event")

    runner = Runner(tmp_path, AsyncMock())
    runner.lock = (tmp_path / "runner.lock").open("a")
    fcntl.flock(runner.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    runner.errors = (tmp_path / "app-server.log").open("ab")
    runner.listener = asyncio.create_task(fail())
    with pytest.raises(ValueError, match="malformed event"):
        await runner.close()
    assert runner.errors.closed
    assert runner.lock.closed
    with (tmp_path / "runner.lock").open("a") as replacement:
        fcntl.flock(replacement, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.anyio
async def test_work_ownership_precedes_ack_and_children_keep_original_work(tmp_path):
    runner = Runner(tmp_path, AsyncMock())

    async def send(text):
        await runner.record(
            {
                "method": "turn/started",
                "params": {
                    "threadId": "root",
                    "turn": {"id": text},
                },
            }
        )
        return text

    runner.session = SimpleNamespace(
        thread_id="root",
        turn_id=None,
        send=AsyncMock(side_effect=send),
        observe=lambda event: None,
    )
    try:
        await runner.send("input-1", "first", work_id="work-1")
        runner.session.turn_id = "first"
        with pytest.raises(RuntimeError, match="still active"):
            await runner.send("input-conflict", "conflict", work_id="work-2")
        await runner.record(
            {
                "method": "thread/started",
                "params": {
                    "thread": {
                        "id": "child",
                        "parentThreadId": "root",
                    }
                },
            }
        )
        runner.session.turn_id = None
        await runner.send("input-2", "second", work_id="work-2")
    finally:
        await runner.close()
    reopened = Runner(tmp_path, AsyncMock())
    try:
        await reopened.record(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "child",
                    "turn": {"id": "child-turn"},
                },
            }
        )
        rows = reopened.journal.read()
        assert [row["record"]["cheese"]["work_id"] for row in rows] == [
            "work-1",
            "work-1",
            "work-2",
            "work-1",
        ]
    finally:
        await reopened.close()
