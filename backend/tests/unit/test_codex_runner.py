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


@pytest.mark.anyio
async def test_a_turn_the_session_was_woken_into_is_its_own_work(tmp_path):
    """A monitor's line said to an idle session starts a turn nobody sent:
    the room opens the books for it when it speaks (`unsolicited`), and the
    next turn the platform sends is the platform's work again."""
    sent = []

    async def send(text):
        sent.append(text)
        return "turn"

    runner = Runner(tmp_path, AsyncMock())
    runner.session = SimpleNamespace(
        send=AsyncMock(side_effect=send),
        thread_id="thread",
        turn_id=None,
        observe=lambda event: None,
    )
    try:
        await runner.tell("<task-notification>line</task-notification>")
        await runner.record(
            {
                "method": "turn/started",
                "params": {"threadId": "thread", "turn": {"id": "turn"}},
            }
        )
        woken = (await runner.dispatch("events", {}))["events"][-1]["record"]
        assert woken["cheese"]["unsolicited"] is True
        own_work = woken["cheese"]["work_id"]

        await runner.send("input", "from the room", work_id="platform-work")
        await runner.record(
            {
                "method": "turn/started",
                "params": {"threadId": "thread", "turn": {"id": "next"}},
            }
        )
        sent_turn = (await runner.dispatch("events", {}))["events"][-1]["record"]
        assert sent_turn["cheese"]["work_id"] == "platform-work" != own_work
        assert "unsolicited" not in sent_turn["cheese"]
    finally:
        await runner.close()


@pytest.mark.anyio
@pytest.mark.parametrize("ending", ["interrupted", "completed"])
async def test_a_person_heard_during_a_woken_turn_gets_a_turn_of_their_own(
    tmp_path, ending
):
    """A person's message that arrives with the room's own work while a turn a
    monitor woke is running is not refused: that turn ends as its own work and
    the message starts the room's, so each turn's records name the work the
    room opened for it. Whether the woken turn stops for it or was finishing
    anyway, the person's turn still owes them a reply."""
    sent = []

    async def send(text):
        sent.append(text)
        runner.session.turn_id = f"turn-{len(sent)}"
        return runner.session.turn_id

    async def interrupt():
        turn = runner.session.turn_id

        async def ends():
            await runner.record(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread",
                        "turn": {"id": turn, "status": ending},
                    },
                }
            )

        asyncio.get_running_loop().call_later(0.05, asyncio.ensure_future, ends())
        return True

    def observe(event):
        if event.get("method") == "turn/completed":
            runner.session.turn_id = None

    runner = Runner(tmp_path, AsyncMock())
    runner.session = SimpleNamespace(
        send=AsyncMock(side_effect=send),
        interrupt=AsyncMock(side_effect=interrupt),
        thread_id="thread",
        turn_id=None,
        observe=observe,
    )
    try:
        await runner.tell("<task-notification>line</task-notification>")
        await runner.record(
            {
                "method": "turn/started",
                "params": {"threadId": "thread", "turn": {"id": "turn-1"}},
            }
        )
        woken = (await runner.dispatch("events", {}))["events"][-1]["record"]
        await runner.send(
            "input", "from a person", work_id="platform-work", owes_reply=True
        )
        assert sent == ["<task-notification>line</task-notification>", "from a person"]
        records = [e["record"] for e in (await runner.dispatch("events", {}))["events"]]
        ended = next(r for r in records if r.get("method") == "turn/completed")
        assert ended["cheese"]["work_id"] == woken["cheese"]["work_id"]
        await runner.record(
            {
                "method": "turn/started",
                "params": {"threadId": "thread", "turn": {"id": "turn-2"}},
            }
        )
        theirs = (await runner.dispatch("events", {}))["events"][-1]["record"]
        assert theirs["cheese"]["work_id"] == "platform-work"
        assert "unsolicited" not in theirs["cheese"]
        assert runner.owed == "input"
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_line_said_mid_turn_goes_into_that_turn(tmp_path):
    runner = Runner(tmp_path, AsyncMock())
    sender = AsyncMock(return_value="turn")
    runner.session = SimpleNamespace(send=sender, thread_id="thread", turn_id="turn")
    runner.journal.remember("work:thread", "platform-work")
    try:
        await runner.tell("<task-notification>line</task-notification>")
        sender.assert_awaited_once()
        assert runner.journal.recall("work:thread") == "platform-work"
    finally:
        await runner.close()


# --- the session compacting itself ----------------------------------------------


@pytest.mark.anyio
async def test_an_idle_thread_is_asked_to_compact_at_once(tmp_path):
    """`cheese_compact` reaches this runner as `request_compact`, and the ask
    goes to app-server's `thread/compact/start`. Nothing is running, so it goes
    now; asked twice, the second is told the first is already on its way."""
    runner = Runner(tmp_path, AsyncMock())
    compacted = AsyncMock()
    runner.session = SimpleNamespace(
        thread_id="thread", turn_id=None, compact=compacted, observe=lambda _: None
    )
    try:
        assert runner.request_compact() == "requested"
        await asyncio.sleep(0.05)
        compacted.assert_awaited_once()
        assert runner.request_compact() == "already"
        assert compacted.await_count == 1
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_request_made_mid_turn_waits_for_that_turn_to_end(tmp_path):
    """`thread/compact/start` replaces whatever task the thread has running, so
    asking mid-turn would cut the turn off: the request waits for
    `turn/completed`. An `item/completed` for the compaction ends it."""
    runner = Runner(tmp_path, AsyncMock())
    compacted = AsyncMock()
    session = SimpleNamespace(
        thread_id="thread", turn_id="turn", compact=compacted, observe=lambda _: None
    )
    runner.session = session
    try:
        assert runner.request_compact() == "requested"
        await asyncio.sleep(0.05)
        assert not compacted.await_count

        await runner.record(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread",
                    "turn": {"id": "turn", "status": "completed"},
                },
            }
        )
        session.turn_id = None
        await asyncio.sleep(0.05)
        compacted.assert_awaited_once()

        # The compaction runs on as a turn of its own; nothing is asked again
        # until it reports its end.
        assert runner.request_compact() == "already"
        await runner.record(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread",
                    "item": {"id": "item-1", "type": "contextCompaction"},
                },
            }
        )
        assert runner.compact_request == ""
    finally:
        await runner.close()
