"""TurnRunner + InProcessBroker: background turns, WS-as-subscriber (design §4)."""

import asyncio
import errno
import time
import uuid

import pytest

from app.domain.agent.runtime import InProcessBroker, TurnRunner


async def _park_a_task() -> asyncio.Task:
    """A real, cancellable task standing in for a turn's `_run` coroutine — the
    sweep cancels wedged turns, so a plain sentinel would not exercise it."""

    async def _never():
        await asyncio.Event().wait()

    task = asyncio.create_task(_never())
    await asyncio.sleep(0)  # let it reach the await, so cancel() is meaningful
    return task


class _FakeChat:
    """Stand-in ChatService.converse: yields a fixed frame sequence, recording
    that it ran even if no one consumes the result."""

    def __init__(self, frames):
        self._frames = frames
        self.ran = False

    async def converse(self, **_):
        self.ran = True
        for f in self._frames:
            await asyncio.sleep(0)  # yield control, like a real streaming turn
            yield f


@pytest.mark.anyio
async def test_broker_fans_out_then_stops_on_unsubscribe():
    broker = InProcessBroker()
    async with broker.subscribe("c") as q:
        await broker.publish("c", {"type": "delta", "text": "hi"})
        assert (await asyncio.wait_for(q.get(), 1))["text"] == "hi"
    # After the context exits the subscription is gone; publishing is a no-op.
    await broker.publish("c", {"type": "delta", "text": "late"})
    assert broker._subs == {}


@pytest.mark.anyio
async def test_reaction_frames_fan_out_but_never_buffer():
    # Reactions are standalone state updates (they can fire between turns):
    # delivered live, but never buffered — otherwise an idle channel would look
    # in_flight forever and dead reactions would replay to late subscribers.
    broker = InProcessBroker()
    async with broker.subscribe("c") as q:
        await broker.publish(
            "c", {"type": "reaction", "block_id": "b1", "reactions": []}
        )
        assert (await asyncio.wait_for(q.get(), 1))["type"] == "reaction"
    assert broker.in_flight("c") is False
    async with broker.subscribe("c", replay=True) as q:
        assert q.empty()


@pytest.mark.anyio
async def test_replay_catches_up_a_mid_turn_subscriber():
    # R3: a connection that subscribes mid-turn gets the in-progress frames.
    broker = InProcessBroker()
    await broker.publish("c", {"type": "user_block"})
    await broker.publish("c", {"type": "delta", "text": "a"})
    async with broker.subscribe("c", replay=True) as q:  # joins mid-turn
        await broker.publish("c", {"type": "delta", "text": "b"})
        got = [q.get_nowait()["type"] for _ in range(3)]
    assert got == ["user_block", "delta", "delta"]  # 2 replayed + 1 live


@pytest.mark.anyio
async def test_buffer_drops_after_turn_so_fresh_subscriber_replays_nothing():
    # Between turns the buffer is empty (the result is persisted as blocks), so a
    # subscriber that connects to start a new turn doesn't replay the dead one.
    broker = InProcessBroker()
    await broker.publish("c", {"type": "user_block"})
    await broker.publish("c", {"type": "done"})
    async with broker.subscribe("c", replay=True) as q:
        assert q.empty()
    assert broker._buffer == {}


@pytest.mark.anyio
async def test_runner_publishes_turn_frames_to_subscribers():
    broker = InProcessBroker()
    runner = TurnRunner(broker)
    chat = _FakeChat(
        [{"type": "user_block"}, {"type": "delta", "text": "x"}, {"type": "done"}]
    )
    topic = uuid.uuid4()
    async with broker.subscribe(str(topic)) as q:
        runner.submit(chat, topic, author="u", content="hi", summon=True)
        seen = []
        while True:
            f = await asyncio.wait_for(q.get(), 1)
            seen.append(f["type"])
            if f["type"] == "done":
                break
    assert seen == ["user_block", "delta", "done"]


class _FakeKickoffChat:
    """Stand-in ChatService.kickoff: the 分身's first turn after a split —
    frames flow with NO human message posted."""

    def __init__(self):
        self.ran = False

    async def kickoff(self, *, topic_id, turn_id=None, prompt=None):
        self.ran = True
        self.prompt = prompt
        await asyncio.sleep(0)
        yield {"type": "delta", "text": "开场白"}
        yield {"type": "done"}


@pytest.mark.anyio
async def test_submit_kickoff_runs_first_turn_without_user_block():
    # 分身自动开工 (spec §8.4): a split sub-topic's first turn starts by itself;
    # the frame stream carries the 分身's own opening, never a user_block.
    broker = InProcessBroker()
    runner = TurnRunner(broker)
    chat = _FakeKickoffChat()
    topic = uuid.uuid4()
    async with broker.subscribe(str(topic)) as q:
        runner.submit_kickoff(chat, topic)
        seen = []
        while True:
            f = await asyncio.wait_for(q.get(), 1)
            seen.append(f["type"])
            if f["type"] == "done":
                break
    assert chat.ran is True
    assert seen == ["delta", "done"]
    assert "user_block" not in seen


@pytest.mark.anyio
async def test_turn_runs_to_completion_without_a_subscriber():
    # The job does not depend on who is watching (invariant 2): no subscriber,
    # the turn still runs.
    broker = InProcessBroker()
    runner = TurnRunner(broker)
    chat = _FakeChat([{"type": "done"}])
    runner.submit(chat, uuid.uuid4(), author="u", content="hi", summon=True)
    for _ in range(50):
        await asyncio.sleep(0)
        if chat.ran:
            break
    assert chat.ran is True


@pytest.mark.anyio
async def test_wedged_turn_times_out_and_is_cancelled():
    # R8: a turn that never finishes must not hold on forever. With a tiny budget
    # it is interrupted (error frame) and the converse generator is cancelled.
    broker = InProcessBroker()
    runner = TurnRunner(broker, turn_timeout_s=0.05)
    cancelled = asyncio.Event()

    class _Hang:
        async def converse(self, **_):
            yield {"type": "user_block"}
            try:
                await asyncio.sleep(10)  # wedge
            except asyncio.CancelledError:
                cancelled.set()
                raise
            yield {"type": "done"}  # pragma: no cover

    topic = uuid.uuid4()
    async with broker.subscribe(str(topic)) as q:
        runner.submit(_Hang(), topic, author="u", content="hi", summon=True)
        kinds = []
        for _ in range(3):
            f = await asyncio.wait_for(q.get(), 1)
            kinds.append(f["type"])
            if f["type"] == "error":
                break
    assert kinds == ["user_block", "error"]
    await asyncio.wait_for(cancelled.wait(), 1)  # the wedged turn was cancelled


# --- turn 活跃度检测 (2026-08-09): `turn_ceiling` reschedules the outer wrap.
# hooks_substrate's two-layer idle-suspect/hard-ceiling logic is pointless if
# THIS outer, transport-independent wrap still kills the turn at the generic
# `agent_turn_timeout_s` regardless of activity — these prove the reschedule
# actually takes effect, is scoped to only the turn that asks for it, and never
# leaks as a visible frame to subscribers.


@pytest.mark.anyio
async def test_turn_ceiling_frame_reschedules_the_outer_timeout():
    """The tmux backend signals its OWN (longer) ceiling via a `turn_ceiling`
    frame — TurnRunner must reschedule its outer wall-clock wrap to that value."""
    broker = InProcessBroker()
    runner = TurnRunner(broker, turn_timeout_s=0.05)  # the generic default

    class _LongTmuxTurn:
        async def converse(self, **_):
            yield {"type": "turn_ceiling", "seconds": 10.0}
            # Longer than the generic 0.05s default, well under the 10s ceiling
            # this turn actually asked for.
            await asyncio.sleep(0.15)
            yield {"type": "done"}

    topic = uuid.uuid4()
    async with broker.subscribe(str(topic)) as q:
        runner.submit(_LongTmuxTurn(), topic, author="u", content="hi", summon=True)
        f = await asyncio.wait_for(q.get(), 2)
    # Never timed out, and the internal control frame never leaked to subscribers.
    assert f["type"] == "done"


@pytest.mark.anyio
async def test_topic_turn_reports_the_rescheduled_ceiling():
    broker = InProcessBroker()
    runner = TurnRunner(broker, turn_timeout_s=0.05)

    class _Turn:
        async def converse(self, **_):
            yield {"type": "turn_ceiling", "seconds": 123.0}
            yield {"type": "done"}

    topic = uuid.uuid4()
    async with broker.subscribe(str(topic)) as q:
        runner.submit(_Turn(), topic, author="u", content="hi", summon=True)
        await asyncio.wait_for(q.get(), 2)
    rec = runner.topic_turn(topic)
    assert rec is not None
    assert rec["ceiling_s"] == 123


@pytest.mark.anyio
async def test_timeout_message_reports_the_effective_ceiling_and_elapsed():
    """F: the timeline message a timed-out turn posts used to drop the actual
    timeout value entirely ("⚠️ 芝士这轮超时被中断了..." with no number) —
    only logger.warning had it, and agent has no host SSH to read logger. The
    message must carry the SAME effective ceiling `topic_turn()`/`cheese
    status` report, plus roughly how long it actually ran."""
    broker = InProcessBroker()
    runner = TurnRunner(broker, turn_timeout_s=1.0)

    class _Hang:
        def __init__(self) -> None:
            self.posted: str | None = None

        async def converse(self, **_):
            yield {"type": "user_block"}
            await asyncio.sleep(10)  # wedge, well past the 1s ceiling
            yield {"type": "done"}  # pragma: no cover

        async def post_system_event(self, topic_id, content, turn_id=None):
            self.posted = content
            return {"id": "b1", "kind": "event", "content": content}

    svc = _Hang()
    topic = uuid.uuid4()
    async with broker.subscribe(str(topic)) as q:
        runner.submit(svc, topic, author="u", content="hi", summon=True)
        for _ in range(3):
            frame = await asyncio.wait_for(q.get(), 3)
            if frame["type"] == "event_block":
                break
    assert svc.posted is not None
    assert "1秒的上限" in svc.posted
    assert "实际跑了约" in svc.posted


@pytest.mark.anyio
async def test_timeout_message_uses_the_rescheduled_ceiling_not_the_generic_default():
    """A turn that rescheduled its ceiling via `turn_ceiling` (turn 活跃度检测,
    e.g. the tmux backend) must have its timeout message report THAT ceiling,
    not the generic outer default — otherwise "was this the generic safety
    net or the backend's real, much longer ceiling" is unanswerable without
    host SSH."""
    broker = InProcessBroker()
    runner = TurnRunner(broker, turn_timeout_s=0.05)  # tiny generic default

    class _Hang:
        def __init__(self) -> None:
            self.posted: str | None = None

        async def converse(self, **_):
            yield {"type": "turn_ceiling", "seconds": 2.0}
            await asyncio.sleep(10)  # wedge, well past the rescheduled 2s
            yield {"type": "done"}  # pragma: no cover

        async def post_system_event(self, topic_id, content, turn_id=None):
            self.posted = content
            return {"id": "b1", "kind": "event", "content": content}

    svc = _Hang()
    topic = uuid.uuid4()
    async with broker.subscribe(str(topic)) as q:
        runner.submit(svc, topic, author="u", content="hi", summon=True)
        for _ in range(3):
            frame = await asyncio.wait_for(q.get(), 5)
            if frame["type"] == "event_block":
                break
    assert svc.posted is not None
    assert "2秒的上限" in svc.posted
    assert "0.05" not in svc.posted


@pytest.mark.anyio
async def test_running_topic_ids_reports_only_in_flight_turns():
    # Bulk signal for the sidebar's「芝士还在跑」indicator: a topic whose turn
    # already finished must drop out, one still mid-flight must show up.
    broker = InProcessBroker()
    runner = TurnRunner(broker)

    class _SlowTurn:
        async def converse(self, **_):
            await asyncio.sleep(0.2)
            yield {"type": "done"}

    finished_topic = uuid.uuid4()
    running_topic = uuid.uuid4()

    async with broker.subscribe(str(finished_topic)) as q:
        runner.submit(
            _FakeChat([{"type": "done"}]),
            finished_topic,
            author="u",
            content="hi",
            summon=True,
        )
        await asyncio.wait_for(q.get(), 1)
    await asyncio.sleep(0.05)  # let the post-loop status flip to "done" land

    runner.submit(_SlowTurn(), running_topic, author="u", content="hi", summon=True)
    await asyncio.sleep(0.05)  # started, but its 0.2s sleep hasn't resolved yet

    ids = runner.running_topic_ids()
    assert running_topic in ids
    assert finished_topic not in ids


@pytest.mark.anyio
async def test_runner_publishes_friendly_error_on_failure():
    broker = InProcessBroker()
    runner = TurnRunner(broker)

    class _Boom:
        async def converse(self, **_):
            raise RuntimeError("kaboom")
            yield  # pragma: no cover — makes this an async generator

    topic = uuid.uuid4()
    async with broker.subscribe(str(topic)) as q:
        runner.submit(_Boom(), topic, author="u", content="hi", summon=True)
        frame = await asyncio.wait_for(q.get(), 1)
    assert frame["type"] == "error"


@pytest.mark.anyio
async def test_turn_failure_lands_in_the_timeline():
    """现场即事实记录: a failed turn persists a system event block (survives
    reload, scrolls with the flow) and marks the error frame persisted=True so
    the client doesn't double-show a banner."""
    broker = InProcessBroker()
    runner = TurnRunner(broker)

    class _Boom:
        def __init__(self) -> None:
            self.posted: str | None = None

        async def converse(self, **_):
            raise RuntimeError("kaboom")
            yield  # pragma: no cover — makes this an async generator

        async def post_system_event(self, topic_id, content, turn_id=None):
            self.posted = content
            return {"id": "b1", "kind": "event", "content": content}

    svc = _Boom()
    topic = uuid.uuid4()
    async with broker.subscribe(str(topic)) as q:
        runner.submit(svc, topic, author="u", content="hi", summon=True)
        first = await asyncio.wait_for(q.get(), 1)
        second = await asyncio.wait_for(q.get(), 1)
    assert first["type"] == "event_block"
    assert "中断" in first["block"]["content"]
    assert second["type"] == "error" and second["persisted"] is True
    assert svc.posted == first["block"]["content"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (OSError(errno.ENOSPC, "No space left on device"), "storage_exhausted"),
        (
            RuntimeError(
                "tmux container create failed: Unable to find image "
                "'cheesex-agent-tmux:latest' locally: pull access denied"
            ),
            "runtime_image_missing",
        ),
    ],
)
async def test_platform_failure_is_coded_and_never_auto_resumes(
    monkeypatch, failure, expected_code
):
    broker = InProcessBroker()
    runner = TurnRunner(broker)

    class _Full:
        def __init__(self) -> None:
            self.meta: dict | None = None

        async def converse(self, **_):
            raise failure
            yield  # pragma: no cover

        async def post_system_event(
            self, topic_id, content, turn_id=None, *, meta=None
        ):
            self.meta = meta
            return {
                "id": "storage-1",
                "kind": "event",
                "content": content,
                "meta": meta,
            }

    def unexpected_resume(*_args, **_kwargs):
        pytest.fail("platform incidents must wait for recovery, not auto-resume")

    monkeypatch.setattr(runner, "_schedule_resume", unexpected_resume)
    svc = _Full()
    topic = uuid.uuid4()
    async with broker.subscribe(str(topic)) as q:
        runner.submit(svc, topic, author="u", content="hi", summon=True)
        event = await asyncio.wait_for(q.get(), 1)
        error = await asyncio.wait_for(q.get(), 1)

    assert event["type"] == "event_block"
    assert event["block"]["meta"]["code"] == expected_code
    assert error == {
        "type": "error",
        "code": expected_code,
        "message": event["block"]["content"],
        "persisted": True,
    }
    assert svc.meta == event["block"]["meta"]


@pytest.mark.anyio
async def test_failed_turn_auto_resumes_once(monkeypatch):
    """续跑: a crashed turn schedules exactly ONE system-nudged continuation;
    the resumed turn carries is_resume=True so it can never chain another."""

    async def _instant(_s):
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)
    broker = InProcessBroker()
    runner = TurnRunner(broker)

    class _Svc:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def converse(self, **kw):
            self.calls.append(kw)
            if len(self.calls) == 1:
                raise RuntimeError("boom")
                yield  # pragma: no cover — makes this an async generator
            yield {"type": "assistant_block", "block": {"id": "a"}}
            yield {"type": "done"}

        async def post_system_event(self, topic_id, content, turn_id=None):
            return {"id": "sys", "kind": "event", "content": content}

    svc = _Svc()
    topic = uuid.uuid4()
    async with broker.subscribe(str(topic)) as q:
        runner.submit(svc, topic, author="u", content="hi", summon=True)
        seen: list[str] = []
        while "assistant_block" not in seen:
            f = await asyncio.wait_for(q.get(), 2)
            seen.append(f["type"])

    assert len(svc.calls) == 2  # original + exactly one auto-resume
    resumed = svc.calls[1]
    assert resumed["is_resume"] is True
    assert resumed["author"] == "system"
    assert "断" in resumed["content"]  # the continuation instruction
    # The failure surfaced first, then the resumed turn's reply.
    assert "error" in seen and seen[-1] == "assistant_block"


@pytest.mark.anyio
async def test_orphan_turns_resume_after_restart(tmp_path, monkeypatch):
    """A turn that was RUNNING when the process died must be swept up on the
    next startup: ⚠️ event posted + an auto-resume scheduled — never silently
    vanish (the deploy-kills-a-turn hole)."""
    import time as _time

    from app.domain.agent import runtime as rt

    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    topic = uuid.uuid4()
    rt._save_inflight(
        {
            "t1": {
                "topic_id": str(topic),
                "started_at": _time.time() - 60,
                "is_resume": False,
            },
            # a resume must never chain another resume, even across restarts
            "t2": {
                "topic_id": str(uuid.uuid4()),
                "started_at": _time.time() - 60,
                "is_resume": True,
            },
            # stale (>2h) entries are dropped, not resurrected
            "t3": {
                "topic_id": str(uuid.uuid4()),
                "started_at": _time.time() - 7300,
                "is_resume": False,
            },
        }
    )

    broker = InProcessBroker()
    runner = TurnRunner(broker)
    scheduled: list[tuple[uuid.UUID, float, str]] = []
    monkeypatch.setattr(
        runner,
        "_schedule_resume",
        lambda _chat, tid, after, why, **_kw: scheduled.append((tid, after, why)),
    )

    class _Chat:
        def __init__(self):
            self.events: list[tuple[uuid.UUID, str]] = []

        async def post_system_event(self, topic_id, text, turn_id=None):
            self.events.append((topic_id, text))
            return {"id": "b1", "content": text}

    chat = _Chat()
    n = await runner.resume_orphans(chat)
    assert n == 1
    assert [s[0] for s in scheduled] == [topic]
    # 宁可吵，不可静默: all three get an event — the resumed one AND the two we
    # refuse to resume. A dropped turn that says nothing is what made a dead
    # topic look exactly like a working one.
    assert len(chat.events) == 3
    dropped = [text for tid, text in chat.events if tid != topic]
    assert len(dropped) == 2
    assert all("@ 芝士" in text for text in dropped)
    # registry cleared: a second sweep is a no-op
    assert await runner.resume_orphans(_Chat()) == 0


@pytest.mark.anyio
async def test_periodic_sweep_claims_turn_killed_without_a_restart(
    tmp_path, monkeypatch
):
    """The 101/173-minute hole: a turn can be killed (container recreate, OOM,
    sandbox swap) while the PROCESS lives on. Nothing then re-reads the registry
    on the startup path, so the sweep must also run periodically — and it must
    tell the live turns apart from the corpses."""
    import time as _time

    from app.domain.agent import runtime as rt

    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    dead_topic = uuid.uuid4()
    live_topic = uuid.uuid4()
    rt._save_inflight(
        {
            "dead": {
                "topic_id": str(dead_topic),
                "started_at": _time.time() - 600,
                "is_resume": False,
            },
            "live": {
                "topic_id": str(live_topic),
                "started_at": _time.time() - 600,
                "is_resume": False,
            },
        }
    )

    runner = TurnRunner(InProcessBroker())
    task = await _park_a_task()  # this process really is running it
    runner._live["live"] = task
    runner._last_frame_at["live"] = time.monotonic()
    scheduled: list[uuid.UUID] = []
    monkeypatch.setattr(
        runner,
        "_schedule_resume",
        lambda _chat, tid, after, why, **_kw: scheduled.append(tid),
    )

    class _Chat:
        async def post_system_event(self, topic_id, text, turn_id=None):
            return {"id": "b1", "content": text}

    assert await runner.sweep_orphans(_Chat()) == 1
    assert scheduled == [dead_topic]
    # The live turn keeps its registry entry — its own completion path owns it.
    assert list(rt._load_inflight()) == ["live"]
    # And a second sweep does not double-resume the one already claimed.
    assert await runner.sweep_orphans(_Chat()) == 0


@pytest.mark.anyio
async def test_periodic_sweep_ignores_a_just_started_turn(tmp_path, monkeypatch):
    """Belt-and-braces against resuming a live turn: an entry younger than
    SWEEP_MIN_AGE_S is never claimed, even if `_live` somehow missed it."""
    import time as _time

    from app.domain.agent import runtime as rt

    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    rt._save_inflight(
        {
            "fresh": {
                "topic_id": str(uuid.uuid4()),
                "started_at": _time.time() - 2,
                "is_resume": False,
            }
        }
    )
    runner = TurnRunner(InProcessBroker())

    class _Chat:
        async def post_system_event(self, topic_id, text, turn_id=None):
            raise AssertionError("a just-started turn must not be touched")

    assert await runner.sweep_orphans(_Chat()) == 0
    assert list(rt._load_inflight()) == ["fresh"]


@pytest.mark.anyio
async def test_stale_orphan_is_dropped_loudly(tmp_path, monkeypatch):
    """Crossing the 2h line must not be a silent `continue`. We still don't
    auto-resume (that part was right) — but the topic has to say so, because a
    human is now the only thing that can move it."""
    import time as _time

    from app.domain.agent import runtime as rt

    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    topic = uuid.uuid4()
    rt._save_inflight(
        {
            "old": {
                "topic_id": str(topic),
                "started_at": _time.time() - 10380,  # 173 min, the real incident
                "is_resume": False,
            }
        }
    )
    broker = InProcessBroker()
    runner = TurnRunner(broker)
    seen: list[dict] = []

    async def _capture(channel, frame):
        seen.append(frame)

    monkeypatch.setattr(broker, "publish", _capture)

    class _Chat:
        def __init__(self):
            self.texts: list[str] = []

        async def post_system_event(self, topic_id, text, turn_id=None):
            self.texts.append(text)
            return {"id": "b1", "content": text}

    chat = _Chat()
    assert await runner.sweep_orphans(chat) == 0  # not resumed...
    assert len(chat.texts) == 1  # ...but not silent either
    assert "173" in chat.texts[0]  # says how long it has been dead
    assert "@ 芝士" in chat.texts[0]  # says what the human can do
    assert rt._load_inflight() == {}  # claimed, so it is not re-announced
    assert [f["type"] for f in seen] == ["event_block"]  # pushed to the UI live


@pytest.mark.anyio
async def test_turn_registers_and_clears_inflight(tmp_path, monkeypatch):
    """While a turn runs it is in the durable registry; after it ends it is
    gone — so only genuinely orphaned turns survive to the next startup."""
    from app.domain.agent import runtime as rt

    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    broker = InProcessBroker()
    runner = TurnRunner(broker)
    chat = _FakeChat([{"type": "done"}])
    runner.submit(chat, uuid.uuid4(), author="u", content="hi", summon=True)
    for _ in range(200):
        await asyncio.sleep(0.01)
        if chat.ran and not rt._load_inflight():
            break
    assert chat.ran
    assert rt._load_inflight() == {}


@pytest.mark.anyio
async def test_in_flight_reflects_replay_buffer():
    """in_flight is true from first published frame until done/error clears
    the buffer — the WS route uses it to tell re-entering clients a turn is
    mid-stream (rebuild 正在思考 instead of showing a dead topic)."""
    broker = InProcessBroker()
    assert broker.in_flight("t") is False
    await broker.publish("t", {"type": "delta", "text": "hi"})
    assert broker.in_flight("t") is True
    await broker.publish("t", {"type": "done"})
    assert broker.in_flight("t") is False


@pytest.mark.anyio
async def test_sweep_claims_a_turn_that_is_live_but_silent(tmp_path, monkeypatch):
    """The 8-hour incident (2026-08-11): three topics went quiet after their last
    block and nothing noticed all night, while this process was up the whole
    time. The task was still in `_live` — what died was the container it drove —
    so `registry - _live` alone sweeps right past it. Silence is the judge."""
    import time as _time
    from datetime import UTC, datetime, timedelta

    from app.domain.agent import runtime as rt

    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    topic = uuid.uuid4()
    rt._save_inflight(
        {
            "wedged": {
                "topic_id": str(topic),
                "started_at": _time.time() - 8 * 3600,
                "is_resume": False,
            }
        }
    )
    runner = TurnRunner(InProcessBroker())
    task = await _park_a_task()
    runner._live["wedged"] = task
    # Both signals cold: no frame for 8h, and the topic's newest block is 8h old.
    runner._last_frame_at["wedged"] = time.monotonic() - 8 * 3600

    async def _last_block(topic_ids):
        assert topic_ids == {topic}
        return {topic: datetime.now(UTC) - timedelta(hours=8)}

    class _Chat:
        def __init__(self):
            self.texts: list[str] = []

        async def post_system_event(self, topic_id, text, turn_id=None):
            self.texts.append(text)
            return {"id": "b1", "content": text}

    chat = _Chat()
    # 8h is past ORPHAN_STALE_S, so it is not auto-resumed — but it must still be
    # torn down and announced, which is the entire point.
    assert await runner.sweep_orphans(chat, last_activity=_last_block) == 0
    await asyncio.sleep(0)
    assert task.cancelled() or task.cancelling()  # the zombie no longer holds the lock
    assert len(chat.texts) == 1
    assert "卡死" in chat.texts[0]  # says HOW it died, not just that it did
    assert "@ 芝士" in chat.texts[0]
    assert rt._load_inflight() == {}


@pytest.mark.anyio
async def test_sweep_spares_a_turn_grinding_through_tools(tmp_path, monkeypatch):
    """A tool call persists no Block, so a turn deep in a tool chain can look
    silent to the DB while being perfectly alive. Cancelling that is worse than
    catching a corpse late — the frame signal is what prevents it."""
    import time as _time
    from datetime import UTC, datetime, timedelta

    from app.domain.agent import runtime as rt

    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    topic = uuid.uuid4()
    rt._save_inflight(
        {
            "busy": {
                "topic_id": str(topic),
                "started_at": _time.time() - 4 * 3600,
                "is_resume": False,
            }
        }
    )
    runner = TurnRunner(InProcessBroker())
    task = await _park_a_task()
    runner._live["busy"] = task
    runner._last_frame_at["busy"] = time.monotonic() - 5  # a tool frame just now

    async def _last_block(topic_ids):
        return {topic: datetime.now(UTC) - timedelta(hours=4)}  # DB says silent

    class _Chat:
        async def post_system_event(self, topic_id, text, turn_id=None):
            raise AssertionError("a turn that is still emitting frames is alive")

    assert await runner.sweep_orphans(_Chat(), last_activity=_last_block) == 0
    assert not task.cancelled()
    assert list(rt._load_inflight()) == ["busy"]  # untouched
    task.cancel()


@pytest.mark.anyio
async def test_sweep_spares_live_turns_when_the_activity_probe_fails(
    tmp_path, monkeypatch
):
    """A DB hiccup must not become a mass cancellation: with no usable evidence
    of silence, a live turn is assumed alive."""
    import time as _time

    from app.domain.agent import runtime as rt

    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    rt._save_inflight(
        {
            "live": {
                "topic_id": str(uuid.uuid4()),
                "started_at": _time.time() - 9 * 3600,
                "is_resume": False,
            }
        }
    )
    runner = TurnRunner(InProcessBroker())
    task = await _park_a_task()
    runner._live["live"] = task
    runner._last_frame_at["live"] = time.monotonic() - 9 * 3600

    async def _boom(topic_ids):
        raise RuntimeError("PG is down")

    class _Chat:
        async def post_system_event(self, topic_id, text, turn_id=None):
            raise AssertionError("no verdict may be reached without evidence")

    assert await runner.sweep_orphans(_Chat(), last_activity=_boom) == 0
    assert not task.cancelled()
    assert list(rt._load_inflight()) == ["live"]
    task.cancel()


@pytest.mark.anyio
async def test_a_wedged_turn_young_enough_to_resume_is_resumed(tmp_path, monkeypatch):
    """Under ORPHAN_STALE_S the wedged turn gets the full treatment: torn down,
    announced, AND continued — nobody has to come back and @ it by hand."""
    import time as _time
    from datetime import UTC, datetime, timedelta

    from app.domain.agent import runtime as rt

    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    topic = uuid.uuid4()
    rt._save_inflight(
        {
            "wedged": {
                "topic_id": str(topic),
                "started_at": _time.time() - 2700,  # 45 min: silent, but not stale
                "is_resume": False,
            }
        }
    )
    runner = TurnRunner(InProcessBroker())
    task = await _park_a_task()
    runner._live["wedged"] = task
    runner._last_frame_at["wedged"] = time.monotonic() - 2700
    scheduled: list[tuple[uuid.UUID, float]] = []
    monkeypatch.setattr(
        runner,
        "_schedule_resume",
        lambda _chat, tid, after, why, **_kw: scheduled.append((tid, after)),
    )

    async def _last_block(topic_ids):
        return {topic: datetime.now(UTC) - timedelta(seconds=2700)}

    class _Chat:
        async def post_system_event(self, topic_id, text, turn_id=None):
            return {"id": "b1", "content": text}

    assert await runner.sweep_orphans(_Chat(), last_activity=_last_block) == 1
    await asyncio.sleep(0)
    assert task.cancelled() or task.cancelling()
    # Resumed with a delay, not instantly: the cancelled task needs to unwind
    # before it lets go of the topic lock.
    assert scheduled == [(topic, 10.0)]


@pytest.mark.anyio
async def test_sweep_keeps_a_turn_that_registered_while_it_was_probing(
    tmp_path, monkeypatch
):
    """The sweep must not delete registry entries it never looked at.

    It loads the registry, then AWAITS the activity probe, then writes back. A
    turn that starts inside that window writes its own entry — and writing back
    the pre-probe snapshot erases it. The turn keeps running with nothing on
    disk, so no later sweep can ever find it: its death would be silent forever,
    which is the exact failure this whole mechanism exists to end.
    """
    import time as _time
    from datetime import UTC, datetime, timedelta

    from app.domain.agent import runtime as rt

    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    dead_topic, new_topic = uuid.uuid4(), uuid.uuid4()
    rt._save_inflight(
        {
            "wedged": {
                "topic_id": str(dead_topic),
                "started_at": _time.time() - 8 * 3600,
                "is_resume": False,
            }
        }
    )
    runner = TurnRunner(InProcessBroker())
    task = await _park_a_task()
    runner._live["wedged"] = task
    runner._last_frame_at["wedged"] = time.monotonic() - 8 * 3600

    async def _last_block(topic_ids):
        # A fresh turn starts while the probe is in flight, exactly as a real
        # `_execute` would: load, add itself, save.
        reg = rt._load_inflight()
        reg["newcomer"] = {
            "topic_id": str(new_topic),
            "started_at": _time.time(),
            "is_resume": False,
        }
        rt._save_inflight(reg)
        return {dead_topic: datetime.now(UTC) - timedelta(hours=8)}

    class _Chat:
        async def post_system_event(self, topic_id, text, turn_id=None):
            return {"id": "b1", "content": text}

    await runner.sweep_orphans(_Chat(), last_activity=_last_block)

    left = rt._load_inflight()
    assert "wedged" not in left  # claimed and announced
    assert "newcomer" in left  # never judged, so never dropped
    task.cancel()


@pytest.mark.anyio
async def test_live_turn_for_topic_tracks_a_running_turn(tmp_path, monkeypatch):
    """The heartbeat half of the stall verdict (`/topics/{id}/status` → `stall`).

    It has to answer from what the process is REALLY executing, not from
    `_recent`: that ring buffer records what turns did, so it keeps saying
    `running` about a turn the process died holding. Here: nothing before the
    turn, a live entry with a fresh frame stamp while it streams, nothing again
    once it ends.
    """
    from app.domain.agent import runtime as rt

    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    broker = InProcessBroker()
    runner = TurnRunner(broker)
    topic = uuid.uuid4()
    assert runner.live_turn_for_topic(topic) is None

    streaming = asyncio.Event()
    finish = asyncio.Event()

    class _Slow:
        async def converse(self, **_):
            yield {"type": "user_block"}
            streaming.set()
            await finish.wait()
            yield {"type": "done"}

    turn_id = runner.submit(_Slow(), topic, author="u", content="hi", summon=True)
    await asyncio.wait_for(streaming.wait(), 1)
    live = runner.live_turn_for_topic(topic)
    assert live is not None
    assert live["turn_id"] == str(turn_id)
    assert live["silent_for_s"] < 5  # a frame just went out
    assert runner.live_turn_for_topic(uuid.uuid4()) is None  # scoped to its topic

    finish.set()
    for _ in range(50):
        await asyncio.sleep(0)
        if runner.live_turn_for_topic(topic) is None:
            break
    assert runner.live_turn_for_topic(topic) is None


@pytest.mark.anyio
async def test_a_killed_turn_stops_claiming_to_be_running(tmp_path, monkeypatch):
    """Tearing a wedged turn down must also END it for every reader.

    The sweep kills a wedged turn with `task.cancel()`. CancelledError is a
    BaseException, so it misses every `except` in `_execute`: the lifecycle
    record stayed `running` forever, and that record is exactly what
    `GET /topics` (`running`) and `/topics/{id}/status` (`turn`) report. The
    platform went on saying 芝士 was working on a turn it had just killed, and
    the broker's replay buffer kept greeting every reconnect with `turn_active`
    — the 8-hour incident's symptom outliving its own fix.
    """
    import time as _time
    from datetime import UTC, datetime, timedelta

    from app.domain.agent import runtime as rt

    monkeypatch.setattr(rt, "_inflight_path", lambda: tmp_path / "inflight.json")
    broker = InProcessBroker()
    runner = TurnRunner(broker, turn_timeout_s=300)
    topic = uuid.uuid4()
    streaming = asyncio.Event()

    class _DeadContainer:
        """A turn whose sandbox died mid-stream: frames stop, the task lives."""

        async def converse(self, **_):
            yield {"type": "user_block"}
            streaming.set()
            await asyncio.sleep(300)

    runner.submit(_DeadContainer(), topic, author="u", content="hi", summon=True)
    await asyncio.wait_for(streaming.wait(), 1)
    assert topic in runner.running_topic_ids()
    assert broker.in_flight(str(topic)) is True

    # Age it into the sweep's sights: both signals cold for eight hours.
    turn_id = next(iter(runner._live))
    runner._last_frame_at[turn_id] = time.monotonic() - 8 * 3600
    reg = rt._load_inflight()
    reg[turn_id]["started_at"] = _time.time() - 8 * 3600
    rt._save_inflight(reg)

    async def _last_block(topic_ids):
        return {topic: datetime.now(UTC) - timedelta(hours=8)}

    class _Chat:
        async def post_system_event(self, topic_id, text, turn_id=None):
            return {"id": "b1", "content": text}

    await runner.sweep_orphans(_Chat(), last_activity=_last_block)
    for _ in range(50):  # let the cancellation land at the turn's await point
        await asyncio.sleep(0)
        if topic not in runner.running_topic_ids():
            break

    assert topic not in runner.running_topic_ids()
    assert runner.topic_turn(topic)["status"] != "running"
    # A reconnecting client must not be told the dead turn is still streaming.
    assert broker.in_flight(str(topic)) is False
