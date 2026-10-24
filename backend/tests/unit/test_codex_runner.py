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
    """`cheese_session(action=compact)` reaches this runner as `request_compact`, and the ask
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
                "method": "turn/started",
                "params": {"threadId": "thread", "turn": {"id": "compact-turn"}},
            }
        )
        await runner.record(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread",
                    "turnId": "compact-turn",
                    "item": {"id": "item-1", "type": "contextCompaction"},
                },
            }
        )
        assert runner.compact_request == ""
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_the_compaction_s_own_turn_is_not_a_room_turn(tmp_path):
    """`thread/compact/start` runs the compaction as a turn of its own on the
    thread. The room must not read its ending: an `AgentResult` there would
    answer, with nothing, work that already ended — one extra answer per
    compaction. The compaction itself still reaches the room, as the
    `contextCompaction` items always did.

    Found by driving the real pinned app-server: it opened a second
    `turn/started`/`turn/completed` pair on the same thread after the ask, and
    the assembled events were `AgentResult("")` behind the real one.
    """
    from app.domain.agent.harness.codex.events import Assembler

    runner = Runner(tmp_path, AsyncMock())
    compacted = AsyncMock()
    session = SimpleNamespace(
        thread_id="thread", turn_id="turn", compact=compacted, observe=lambda _: None
    )
    runner.session = session
    try:
        runner.journal.remember("work:thread", "platform-work")
        assert runner.request_compact() == "requested"
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

        # What app-server reports for the compaction: a turn of its own, with
        # the compaction's items inside it.
        for event in (
            {
                "method": "turn/started",
                "params": {"threadId": "thread", "turn": {"id": "ct"}},
            },
            {
                "method": "item/started",
                "params": {
                    "threadId": "thread",
                    "turnId": "ct",
                    "item": {"id": "c1", "type": "contextCompaction"},
                },
            },
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread",
                    "turnId": "ct",
                    "item": {"id": "c1", "type": "contextCompaction"},
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread",
                    "turn": {"id": "ct", "status": "completed"},
                },
            },
        ):
            await runner.record(event)

        records = [e["record"] for e in (await runner.dispatch("events", {}))["events"]]
        turns = [
            r for r in records if r.get("method") in ("turn/started", "turn/completed")
        ]
        assert [r["params"]["turn"]["id"] for r in turns] == ["turn"]

        assembler = Assembler()
        assembled = [event for r in records for event in assembler.accept(r)]
        assert [e for e in assembled if type(e).__name__ == "AgentCompacting"], (
            "the compaction itself must still reach the room"
        )
        results = [e for e in assembled if type(e).__name__ == "AgentResult"]
        # Exactly one: the real turn's (which says nothing here, because the
        # fixture gave that turn no message). A second would be the phantom.
        assert len(results) == 1, results
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_turn_replacing_the_compaction_clears_the_request(tmp_path):
    """A person can write while the compaction runs; app-server swaps the task,
    and the compaction's turn then never completes. Without clearing the state,
    the request would sit at `sent` for good — and a session that never reports
    it finished is a session that never goes idle."""
    runner = Runner(tmp_path, AsyncMock())
    compacted = AsyncMock()
    session = SimpleNamespace(
        thread_id="thread", turn_id=None, compact=compacted, observe=lambda _: None
    )
    runner.session = session
    try:
        runner.request_compact()
        await asyncio.sleep(0.05)
        await runner.record(
            {
                "method": "turn/started",
                "params": {"threadId": "thread", "turn": {"id": "compact-turn"}},
            }
        )
        await runner.record(
            {
                "method": "item/started",
                "params": {
                    "threadId": "thread",
                    "turnId": "compact-turn",
                    "item": {"id": "c1", "type": "contextCompaction"},
                },
            }
        )
        assert runner.compacting_turn == "compact-turn"

        # A person's turn ends; the compaction's own never comes.
        await runner.record(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread",
                    "turn": {"id": "person-turn", "status": "completed"},
                },
            }
        )
        assert runner.compacting_turn is None
        assert runner.compact_request == ""
        assert runner.busy() is False
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_person_s_turn_beside_the_ask_is_not_mistaken_for_it(tmp_path):
    """A person's message can arrive beside the ask, putting their
    `turn/started` first. Identifying the compaction's turn by order would
    swallow their turn — its ending, its `turn_ended`, and the answer it owes —
    so the compaction is recognised by its own `contextCompaction` item and
    their turn goes through untouched."""
    runner = Runner(tmp_path, AsyncMock())
    compacted = AsyncMock()
    session = SimpleNamespace(
        thread_id="thread", turn_id=None, compact=compacted, observe=lambda _: None
    )
    runner.session = session
    try:
        runner.journal.remember("work:thread", "platform-work")
        runner.request_compact()
        await asyncio.sleep(0.05)

        # The person's turn comes first, and ends. This is the id `send`
        # answered when the room started it.
        runner.room_turns.add("person-turn")
        await runner.record(
            {
                "method": "turn/started",
                "params": {"threadId": "thread", "turn": {"id": "person-turn"}},
            }
        )
        await runner.record(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread",
                    "turn": {"id": "person-turn", "status": "completed"},
                },
            }
        )

        # Their turn is the room's: it is journaled, with its ending.
        records = [e["record"] for e in (await runner.dispatch("events", {}))["events"]]
        assert [
            r["params"]["turn"]["id"]
            for r in records
            if r.get("method") == "turn/started"
        ] == ["person-turn"]
        assert [
            r["params"]["turn"]["id"]
            for r in records
            if r.get("method") == "turn/completed"
        ] == ["person-turn"]
        # Their turn replaced the compaction, which will now never report: the
        # request goes, so the seat can go idle.
        assert runner.compact_request == ""

    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_compaction_the_session_did_not_ask_for_keeps_its_turn(tmp_path):
    """Codex compacts by itself when the context nears its limit, before or
    partway through a turn. That compaction is the turn's own work: its item
    names the turn it happened in, so reading it as the platform's would drop
    that turn's ending — the room's answer — with no request involved at all.

    Seen in the pinned build: `run_inline_auto_compact_task` reuses the running
    turn's context and sends no `turn/started` of its own.
    """
    from app.domain.agent.harness.codex.events import Assembler

    runner = Runner(tmp_path, AsyncMock())
    session = SimpleNamespace(
        thread_id="thread",
        turn_id="turn",
        compact=AsyncMock(),
        observe=lambda _: None,
    )
    runner.session = session
    try:
        runner.journal.remember("work:thread", "platform-work")
        # What the runner does when it starts a turn for the room.
        runner.room_turns.add("turn")
        for event in (
            {
                "method": "turn/started",
                "params": {"threadId": "thread", "turn": {"id": "turn"}},
            },
            {
                "method": "item/started",
                "params": {
                    "threadId": "thread",
                    "turnId": "turn",
                    "item": {"id": "c1", "type": "contextCompaction"},
                },
            },
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread",
                    "turnId": "turn",
                    "item": {"id": "c1", "type": "contextCompaction"},
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread",
                    "turn": {
                        "id": "turn",
                        "status": "completed",
                        "items": [
                            {"type": "agentMessage", "text": "the answer"},
                        ],
                    },
                },
            },
        ):
            await runner.record(event)

        assert runner.compact_request == ""
        records = [e["record"] for e in (await runner.dispatch("events", {}))["events"]]
        assembler = Assembler()
        assembled = [event for r in records for event in assembler.accept(r)]
        results = [e for e in assembled if type(e).__name__ == "AgentResult"]
        assert [e.text for e in results] == ["the answer"], results
        assert [e for e in assembled if type(e).__name__ == "AgentCompacting"], (
            "the compaction itself still reaches the room"
        )
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_compaction_the_session_did_not_ask_for_leaves_the_ask_alone(tmp_path):
    """The agent asks to compact in a turn that then compacts by itself: the
    automatic item is not the platform's, and must not answer for it — the ask
    still goes when the turn ends."""
    runner = Runner(tmp_path, AsyncMock())
    compacted = AsyncMock()
    session = SimpleNamespace(
        thread_id="thread", turn_id="turn", compact=compacted, observe=lambda _: None
    )
    runner.session = session
    try:
        # What the runner does when it starts a turn for the room.
        runner.room_turns.add("turn")
        assert runner.request_compact() == "requested"
        await asyncio.sleep(0.05)
        assert not compacted.await_count

        await runner.record(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread",
                    "turnId": "turn",
                    "item": {"id": "c1", "type": "contextCompaction"},
                },
            }
        )
        assert runner.compact_request == "asked"

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
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_turn_beside_the_ask_that_compacts_itself_keeps_its_answer(tmp_path):
    """A person's message can replace the compaction the ask just started, and
    the turn that replaces it is likely to compact by itself — the history it
    inherits is the one nobody summarised. That automatic item names their
    turn, which the room started: reading it as the compaction's would drop
    their answer."""
    from app.domain.agent.harness.codex.events import Assembler

    runner = Runner(tmp_path, AsyncMock())
    compacted = AsyncMock()
    session = SimpleNamespace(
        thread_id="thread", turn_id=None, compact=compacted, observe=lambda _: None
    )
    runner.session = session
    try:
        runner.journal.remember("work:thread", "platform-work")
        assert runner.request_compact() == "requested"
        await asyncio.sleep(0.05)
        compacted.assert_awaited_once()

        # Their turn replaces the compaction; this is the id `send` answered.
        runner.room_turns.add("person-turn")
        for event in (
            {
                "method": "turn/started",
                "params": {"threadId": "thread", "turn": {"id": "person-turn"}},
            },
            {
                "method": "item/started",
                "params": {
                    "threadId": "thread",
                    "turnId": "person-turn",
                    "item": {"id": "c1", "type": "contextCompaction"},
                },
            },
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread",
                    "turnId": "person-turn",
                    "item": {"id": "c1", "type": "contextCompaction"},
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread",
                    "turn": {
                        "id": "person-turn",
                        "status": "completed",
                        "items": [{"type": "agentMessage", "text": "their answer"}],
                    },
                },
            },
        ):
            await runner.record(event)

        assert runner.compacting_turn is None
        assert runner.compact_request == ""
        records = [e["record"] for e in (await runner.dispatch("events", {}))["events"]]
        assert [
            r["params"]["turn"]["id"]
            for r in records
            if r.get("method") == "turn/completed"
        ] == ["person-turn"]
        assembler = Assembler()
        assembled = [event for r in records for event in assembler.accept(r)]
        results = [e for e in assembled if type(e).__name__ == "AgentResult"]
        assert [e.text for e in results] == ["their answer"], results
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_room_turn_is_known_before_the_send_answers(tmp_path):
    """app-server can push a turn's `turn/started` before the send that opened
    it answers — the same reason the work id is pinned before the call. An id
    nothing has answered for yet must still count as the room's, or the
    automatic compaction at that turn's start reads as the platform's and the
    room loses the answer."""
    from app.domain.agent.harness.codex.events import Assembler

    runner = Runner(tmp_path, AsyncMock())
    entered, release = asyncio.Event(), asyncio.Event()

    async def send(text):
        entered.set()
        await release.wait()
        return "person-turn"

    session = SimpleNamespace(
        thread_id="thread",
        turn_id=None,
        compact=AsyncMock(),
        observe=lambda _: None,
        send=send,
    )
    runner.session = session
    try:
        runner.journal.remember("work:thread", "platform-work")
        assert runner.request_compact() == "requested"
        await asyncio.sleep(0.05)

        sending = asyncio.create_task(runner.send("input", "hi", work_id="w2"))
        await entered.wait()

        # The turn is announced before the send comes back with its id.
        await runner.record(
            {
                "method": "turn/started",
                "params": {"threadId": "thread", "turn": {"id": "person-turn"}},
            }
        )
        await runner.record(
            {
                "method": "item/started",
                "params": {
                    "threadId": "thread",
                    "turnId": "person-turn",
                    "item": {"id": "c1", "type": "contextCompaction"},
                },
            }
        )
        await runner.record(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread",
                    "turnId": "person-turn",
                    "item": {"id": "c1", "type": "contextCompaction"},
                },
            }
        )

        release.set()
        await sending
        await runner.record(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread",
                    "turn": {
                        "id": "person-turn",
                        "status": "completed",
                        "items": [{"type": "agentMessage", "text": "the answer"}],
                    },
                },
            }
        )

        assert runner.compact_request == ""
        records = [e["record"] for e in (await runner.dispatch("events", {}))["events"]]
        assembler = Assembler()
        assembled = [event for r in records for event in assembler.accept(r)]
        results = [e for e in assembled if type(e).__name__ == "AgentResult"]
        assert [e.text for e in results] == ["the answer"], results
    finally:
        release.set()
        await runner.close()


@pytest.mark.anyio
async def test_a_turn_interrupted_beside_the_ask_does_not_leave_it_hanging(tmp_path):
    """A room turn that started while the ask was outstanding and was then
    interrupted — by a person, not by the compaction — is not a compaction
    replaced: nothing will report for it, so the ask must go with it. Held
    back, it would keep `busy()` true and the seat would never go idle."""
    runner = Runner(tmp_path, AsyncMock())
    session = SimpleNamespace(
        thread_id="thread", turn_id=None, compact=AsyncMock(), observe=lambda _: None
    )
    runner.session = session
    try:
        assert runner.request_compact() == "requested"
        await asyncio.sleep(0.05)
        assert runner.compact_request == "sent"

        runner.room_turns.add("person-turn")
        await runner.record(
            {
                "method": "turn/started",
                "params": {"threadId": "thread", "turn": {"id": "person-turn"}},
            }
        )
        await runner.record(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread",
                    "turn": {"id": "person-turn", "status": "interrupted"},
                },
            }
        )
        assert runner.compact_request == ""
        assert runner.busy() is False
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_child_thread_ending_is_not_dropped_by_the_compaction(tmp_path):
    """A compaction runs for minutes; a sub-agent's thread can end meanwhile.
    Its `turn/completed` belongs to that thread and must reach the journal —
    the room flushes what the child had partially said on it."""
    runner = Runner(tmp_path, AsyncMock())
    session = SimpleNamespace(
        thread_id="thread",
        turn_id=None,
        compact=AsyncMock(),
        observe=lambda _: None,
    )
    runner.session = session
    try:
        runner.request_compact()
        await asyncio.sleep(0.05)
        for event in (
            {
                "method": "turn/started",
                "params": {"threadId": "thread", "turn": {"id": "compact-turn"}},
            },
            {
                "method": "item/started",
                "params": {
                    "threadId": "thread",
                    "turnId": "compact-turn",
                    "item": {"id": "c1", "type": "contextCompaction"},
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "child",
                    "turn": {"id": "child-turn", "status": "completed"},
                },
            },
        ):
            await runner.record(event)

        records = [e["record"] for e in (await runner.dispatch("events", {}))["events"]]
        assert [
            r["params"]["turn"]["id"]
            for r in records
            if r.get("method") == "turn/completed"
        ] == ["child-turn"]
        # The child's ending is not the compaction's: it stays open.
        assert runner.compacting_turn == "compact-turn"
    finally:
        await runner.close()
