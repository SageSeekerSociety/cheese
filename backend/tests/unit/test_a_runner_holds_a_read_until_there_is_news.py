"""A runner asked to wait holds a read until it has something to answer it
with, and never past the session it belongs to.

Reached the way a backend reaches it: over the runner's socket, one JSON line
each way.
"""

import asyncio
import time
from pathlib import Path

import pytest

from app.domain.agent.harness.driven import runner
from tests.unit.test_driven_harness import Journal, call

#: How long the held reads below ask to wait. An answer well inside it came from
#: the news, not from the wait running out; how far inside depends on how busy
#: the machine is, so the bound is a share of the wait, not a latency target.
HOLD_S = 30
PROMPT_S = HOLD_S / 6


class Session(runner.Runner[Journal]):
    """A session that writes when told to, read the way a backend reads it."""

    def __init__(self, state: Path, **options):
        super().__init__(state, Journal, "records.sqlite", **options)

    async def start(self) -> None:
        self.claim()
        await self.listen(2**16)

    def busy(self) -> bool:
        return False

    async def dispatch(self, method: str, params: dict) -> dict:
        if method == "events":
            after = int(params.get("after", 0))
            news = await self.news_for(after, params)
            return {"events": self.records(after), **news}
        raise ValueError(method)


@pytest.mark.anyio
async def test_a_held_read_is_answered_when_a_record_is_written_and_not_before(
    tmp_path,
):
    session = Session(tmp_path / "state", idle_exit_s=0)
    await session.start()
    try:
        read = asyncio.create_task(
            call(session.state, "events", {"after": 0, "wait": 30, "live": None})
        )
        first = await asyncio.wait_for(read, 5)
        # Nothing written, but the reader had never seen what is being shown.
        assert first["events"] == [] and "live" in first

        mark = first["live"]["mark"]
        read = asyncio.create_task(
            call(session.state, "events", {"after": 0, "wait": HOLD_S, "live": mark})
        )
        await asyncio.sleep(0.5)
        assert not read.done()

        session.journal.append({"said": "done"})
        answer = await asyncio.wait_for(read, PROMPT_S)
        assert [row["record"] for row in answer["events"]] == [{"said": "done"}]
    finally:
        await session.close()


@pytest.mark.anyio
async def test_a_read_that_does_not_ask_to_wait_is_answered_at_once(tmp_path):
    session = Session(tmp_path / "state", idle_exit_s=0)
    await session.start()
    try:
        answer = await asyncio.wait_for(call(session.state, "events", {"after": 0}), 1)
        assert answer == {"events": []}
    finally:
        await session.close()


@pytest.mark.anyio
async def test_what_the_agent_is_writing_answers_a_held_read(tmp_path):
    session = Session(tmp_path / "state", idle_exit_s=0)
    await session.start()
    try:
        mark = (
            await call(session.state, "events", {"after": 0, "wait": 1, "live": None})
        )["live"]["mark"]
        read = asyncio.create_task(
            call(session.state, "events", {"after": 0, "wait": 30, "live": mark})
        )
        await asyncio.sleep(0.2)
        assert not read.done()

        session.show([{"type": "text", "text": "Hel"}], "work-1")
        answer = await asyncio.wait_for(read, 5)
        assert answer["events"] == []
        assert answer["live"]["blocks"] == [{"type": "text", "text": "Hel"}]
        assert answer["live"]["work_id"] == "work-1"

        # Seen, and unchanged since: not news.
        seen = answer["live"]["mark"]
        quiet = await call(
            session.state, "events", {"after": 0, "wait": 0.3, "live": seen}
        )
        assert "live" not in quiet
    finally:
        await session.close()


@pytest.mark.anyio
async def test_the_agent_process_ending_answers_a_held_read_at_once(tmp_path):
    """A crash that writes nothing is still news: the read held at the runner
    says at once that the agent process is gone, not when its wait runs out."""
    session = Session(tmp_path / "state", idle_exit_s=0)
    session.process = await asyncio.create_subprocess_exec("sleep", "60")
    await session.start()
    try:
        mark = session.live_mark()
        held = asyncio.create_task(
            call(session.state, "events", {"after": 0, "wait": HOLD_S, "live": mark})
        )
        await asyncio.sleep(0.3)
        assert not held.done()

        session.process.kill()
        answer = await asyncio.wait_for(held, PROMPT_S)
        assert answer["alive"] is False
    finally:
        await session.close()


@pytest.mark.anyio
async def test_an_idle_session_is_let_go_with_a_read_held_on_it(tmp_path):
    """Being read is not being used, and a read that waits is no exception: the
    session goes, and the read it held is answered rather than left hanging."""
    session = Session(tmp_path / "state", idle_exit_s=0.3)
    session.process = await asyncio.create_subprocess_exec("sleep", "60")
    await session.start()
    closed = False
    try:
        mark = session.live_mark()
        held = asyncio.create_task(
            call(session.state, "events", {"after": 0, "wait": 60, "live": mark})
        )
        # The session is let go: its process is ended (``release``)...
        await asyncio.wait_for(session.process.wait(), 15)
        # ...and the runner closing on that answers the read it still held.
        closing = time.monotonic()
        await asyncio.wait_for(session.close(), 5)
        closed = True
        answer = await asyncio.wait_for(held, 1)
        assert time.monotonic() - closing < 2
        assert answer["alive"] is False
    finally:
        if not closed:
            await session.close()


@pytest.mark.anyio
async def test_a_runner_that_starts_closing_says_its_agent_is_gone(tmp_path):
    """A runner on its way out answers the read it holds before it stops its
    agent process. That answer must not say the agent is there: the backend
    would hand the seat to the next send, which the closed socket refuses."""
    session = Session(tmp_path / "state", idle_exit_s=0)
    session.process = await asyncio.create_subprocess_exec("sleep", "60")
    await session.start()
    mark = session.live_mark()
    held = asyncio.create_task(
        call(session.state, "events", {"after": 0, "wait": 30, "live": mark})
    )
    await asyncio.sleep(0.3)
    await asyncio.wait_for(session.close(), 5)
    answer = await asyncio.wait_for(held, 1)
    assert answer["alive"] is False
