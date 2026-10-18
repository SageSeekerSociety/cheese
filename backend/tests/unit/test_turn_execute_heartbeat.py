"""The last frame a turn's session produced: what it was, and how old.

Two halves meet here. A turn's own record (`turn.state.execution.recent`)
learns the kind of the last frame and this process's monotonic reading of it,
recorded before the frame loop's own `continue`s so a lifecycle frame counts
too. A driven session's runner keeps the same fact at the moment it reads the
frame, and the backend copies it through the subscription without ever
subtracting one machine's monotonic clock from another's.
"""

import asyncio
import json
import time
import uuid
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.routes import run_records
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.claude_code import runner as claude_runner
from app.domain.agent.harness.driven import subscription as subscription_module
from app.domain.agent.harness.driven.subscription import Subscription
from app.domain.agent.session_host.host import SessionHost
from app.domain.agent.turn.state.execution import ExecutionPolicy, ExecutionState
from app.domain.agent.turn.steps import execute as execute_module
from app.domain.agent.turn.steps.execute import ExecutionEffects, execute


def _state() -> ExecutionState:
    return ExecutionState(
        recent=deque(),
        live={},
        last_frame_at={},
        live_topics={},
        delivered=set(),
        host_failed_topics=set(),
    )


def _effects() -> ExecutionEffects:
    async def nothing(*args, **kwargs):
        return None

    async def no_event(*args, **kwargs):
        return None

    return ExecutionEffects(
        post_system_event=no_event,
        stamp_delivery=nothing,
        recover_silent_turn=nothing,
        record_host_success=nothing,
        handle_host_failure=nothing,
        credential_is_known_expired=lambda topic: False,
        session_took_over=lambda topic, turn: False,
    )


async def _run(
    monkeypatch, frames: list[dict], *, summon: bool = False
) -> ExecutionState:
    state = _state()
    published: list[dict] = []

    async def publish(channel: str, frame: dict) -> None:
        published.append(frame)

    async def frame_source():
        for frame in frames:
            yield frame

    async def open_turn(*args, **kwargs) -> None:
        return None

    async def close_turns(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(execute_module, "open_turn", open_turn)
    monkeypatch.setattr(execute_module, "close_turns", close_turns)
    await execute(
        state,
        ExecutionPolicy(
            timeout=60,
            first_output_timeout=0,
            credential_expired_fuse=1,
            resend_reason="",
            expected_error_types=(),
        ),
        _effects(),
        None,  # type: ignore[arg-type] — the two store calls are stubbed above
        publish,
        frame_source,
        uuid.uuid4(),
        uuid.uuid4(),
        author="alice",
        content="做点事",
        summon=summon,
    )
    return state


@pytest.mark.anyio
async def test_a_lifecycle_frame_is_recorded_before_its_continue(monkeypatch) -> None:
    """`session_lifecycle` takes its own branch and never reaches the generic
    path. The watchdog and the record are updated before that branch, so the
    frame still says the session is there — and it must not count as first
    output (the cold-start fuse is retired only by `assistant_block`)."""
    state = await _run(monkeypatch, [{"type": "session_lifecycle"}])

    record = state.recent[0]
    assert record["last_frame_kind"] == "session_lifecycle"
    assert isinstance(record["last_frame_at"], float)
    assert record["first_output_s"] is None


@pytest.mark.anyio
async def test_a_system_frame_is_named_by_its_subtype(monkeypatch) -> None:
    state = await _run(monkeypatch, [{"type": "system", "subtype": "init"}])

    assert state.recent[0]["last_frame_kind"] == "init"


@pytest.mark.anyio
async def test_the_last_frame_is_the_one_reported(monkeypatch) -> None:
    state = await _run(
        monkeypatch,
        [
            {"type": "system", "subtype": "init"},
            {"type": "assistant_block", "text": "在"},
        ],
    )

    record = state.recent[0]
    assert record["last_frame_kind"] == "assistant_block"
    assert record["first_output_s"] is not None


@pytest.mark.anyio
async def test_a_turn_with_no_frame_reports_unknown_not_zero(monkeypatch) -> None:
    state = await _run(monkeypatch, [])

    record = state.recent[0]
    assert record["last_frame_kind"] is None
    assert record["last_frame_at"] is None


@pytest.mark.anyio
async def test_the_runner_heartbeat_names_the_frame_and_its_work(tmp_path) -> None:
    """`keep_alive` and `thinking_tokens` stay out of the journal, yet both are
    frames a quiet session publishes — so the heartbeat must see them."""
    session = claude_runner.Runner(tmp_path / "state", idle_exit_s=0)
    session.session_id = "session-1"
    session.working, session.work = True, "turn-1"

    reader = asyncio.StreamReader()
    for frame in (
        {"type": "keep_alive"},
        {"type": "system", "subtype": "thinking_tokens"},
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        },
    ):
        reader.feed_data((json.dumps(frame) + "\n").encode())
    reader.feed_eof()
    session.process = SimpleNamespace(  # type: ignore[assignment]
        stdout=reader, returncode=None, pid=1
    )

    await session._read()

    assert session.journal.last() == 0
    heartbeat = session.heartbeat()
    assert heartbeat["last_frame_kind"] == "stream_event"
    assert heartbeat["work_id"] == "turn-1"
    assert heartbeat["last_frame_age_s"] is not None
    assert heartbeat["last_frame_age_s"] >= 0

    # An ended work is not a live one, and a new work does not inherit the old
    # work's frame: both read as "no work", never as `last_work`.
    session._end()
    assert session.heartbeat()["work_id"] is None
    session.working, session.work = True, "turn-2"
    assert session.heartbeat()["work_id"] is None
    session.journal.close()


@pytest.mark.anyio
async def test_every_read_path_keeps_the_heartbeat(monkeypatch) -> None:
    """The heartbeat is taken at the one public entry, not only at a helper:
    an ordinary read, a held (asking) read, a hurried read and a read given
    way to (cancelled from outside and re-read) all carry it. `drain` clears
    `heard` at the start of every pass; the frame must survive that too.

    The subscription's own clock is a controlled stand-in (`subscription.time`,
    not the loop's): the runner reports an age of 4 s, this backend hears it,
    then 7 s pass here — the age must read exactly 11, not merely "between 4
    and 6". A version that drops the local elapsed term reads 4 and fails.
    """
    calls: list[dict] = []
    held = asyncio.Event()
    clock = [100.0]
    monkeypatch.setattr(
        subscription_module,
        "time",
        SimpleNamespace(monotonic=lambda: clock[0]),
    )

    async def call(method: str, params: dict) -> dict:
        calls.append(dict(params))
        if params.get("hold"):
            held.set()
            await asyncio.Event().wait()
        return {
            "heartbeat": {
                "session_id": "session-1",
                "work_id": f"turn-{len(calls)}",
                "last_frame_kind": "assistant_block",
                # The runner measured 4 s ago on its own monotonic clock.
                "last_frame_age_s": 4.0,
            }
        }

    async def consume(*args, **kwargs) -> None:
        return None

    async def activity(*args, **kwargs) -> None:
        return None

    session = SessionRef(
        project_id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        agent_handle="alice",
        harness="claude_code",
    )
    subscription = Subscription(session, Path("/tmp"), call, consume, activity)
    try:
        # Ordinary: no wait to merge, answered at once.
        await subscription.read("events", {"after": 0})
        assert calls[-1] == {"after": 0}
        assert subscription.frame()["work_id"] == "turn-1"

        # Asking: the drain's wait and live mark are merged into the call.
        subscription.asking = {"wait": 25, "live": "mark"}
        await subscription.read("events", {"after": 0})
        assert calls[-1] == {"after": 0, "wait": 25, "live": "mark"}
        assert subscription.frame()["work_id"] == "turn-2"

        # `drain` clears `heard` between passes; the frame stays.
        subscription.heard = {}
        assert subscription.frame() is not None

        # Hurried: an asking read that must not wait, read at once instead.
        subscription.asking = {"wait": 25}
        subscription.hurry = True
        await subscription.read("events", {"after": 0})
        assert calls[-1] == {"after": 0}
        assert subscription.hurry is False
        assert subscription.frame()["work_id"] == "turn-3"

        # Given way: the production entry for this is `unpark`, which sets
        # `hurry` and cancels the held read. The cancelled read then re-reads
        # at once without the wait. A direct `parked.cancel()` would skip the
        # `hurry` half the next drain depends on.
        subscription.asking = {"wait": 25, "hold": True}
        task = asyncio.ensure_future(subscription.read("events", {"after": 0}))
        await asyncio.wait_for(held.wait(), 1)
        assert subscription.parked is not None
        subscription.unpark()
        assert subscription.hurry is True
        await asyncio.wait_for(task, 1)
        assert calls[-1] == {"after": 0}
        assert subscription.frame()["work_id"] == f"turn-{len(calls)}"
        # The hurried state outlives the cancelled read: the next asking read
        # is answered at once too, and only then clears it.
        assert subscription.hurry is True
        subscription.asking = {"wait": 25}
        await subscription.read("events", {"after": 0})
        assert calls[-1] == {"after": 0}
        assert subscription.hurry is False
        assert subscription.frame()["work_id"] == f"turn-{len(calls)}"

        frame = subscription.frame()
        assert frame["last_frame_kind"] == "assistant_block"
        # The runner said 4 s at the moment this backend heard it; 7 s pass on
        # this clock, so exactly 11. One machine's monotonic is never
        # subtracted from another's — only local elapsed time is added.
        assert frame["last_frame_age_s"] == 4.0
        clock[0] += 7.0
        assert subscription.frame()["last_frame_age_s"] == 11.0
    finally:
        subscription.disk.shutdown(wait=True)


def test_the_host_answers_only_live_work_frames() -> None:
    turn, other, finished = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    host = SessionHost()

    class SubscriptionStub:
        def __init__(self, work: str | None):
            self.work = work

        def frame(self) -> dict | None:
            if self.work is None:
                return None
            return {
                "work_id": self.work,
                "last_frame_kind": "assistant_block",
                "last_frame_age_s": 2.0,
            }

    host._running = {  # type: ignore[assignment]
        "live": SimpleNamespace(subscription=SubscriptionStub(str(turn))),
        "gone": SimpleNamespace(subscription=SubscriptionStub(None)),
    }

    found = host.frame_snapshots([turn, other, finished])

    assert found == {
        turn: {
            "work_id": str(turn),
            "last_frame_kind": "assistant_block",
            "last_frame_age_s": 2.0,
        }
    }


def test_the_route_fills_open_turns_from_this_process_only() -> None:
    turn, closed, missing = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    payload = {
        "turns": [
            {"turn_id": str(turn), "stopped_at": None},
            {"turn_id": str(closed), "stopped_at": "2026-10-17T00:00:00+00:00"},
            {"turn_id": str(missing), "stopped_at": None},
        ]
    }

    class HostStub:
        def frame_snapshots(self, turn_ids):
            assert set(turn_ids) == {turn, missing}
            return {
                turn: {
                    "work_id": str(turn),
                    "last_frame_kind": "assistant_block",
                    "last_frame_age_s": 3.5,
                }
            }

    class RunnerStub:
        def recent_work(self):
            return [
                # A closed turn's stale record must not be read as life.
                {
                    "turn_id": str(closed),
                    "status": "done",
                    "last_frame_kind": "tool",
                    "last_frame_at": time.monotonic(),
                }
            ]

    run_records._add_frames(payload, host=HostStub(), runner=RunnerStub())

    live, shut, unknown = payload["turns"]
    assert live["last_frame_kind"] == "assistant_block"
    assert live["last_frame_age_s"] == 3.5
    # Closed: not filled, even though a record exists.
    assert shut.get("last_frame_kind") is None
    assert shut.get("last_frame_age_s") is None
    # Nobody here knows: unknown, never 0.
    assert unknown["last_frame_kind"] is None
    assert unknown["last_frame_age_s"] is None


def test_recent_work_fills_an_open_turn_without_calling_it_alive() -> None:
    turn = uuid.uuid4()
    payload = {"turns": [{"turn_id": str(turn), "stopped_at": None}]}

    class HostStub:
        def frame_snapshots(self, turn_ids):
            return {}

    class RunnerStub:
        def recent_work(self):
            return [
                {
                    "turn_id": str(turn),
                    # The status is an older moment's opinion; the frame still
                    # carries a real age, and that is all this read uses.
                    "status": "done",
                    "last_frame_kind": "tool",
                    "last_frame_at": time.monotonic() - 12,
                }
            ]

    run_records._add_frames(payload, host=HostStub(), runner=RunnerStub())

    filled = payload["turns"][0]
    assert filled["last_frame_kind"] == "tool"
    assert filled["last_frame_age_s"] >= 12
