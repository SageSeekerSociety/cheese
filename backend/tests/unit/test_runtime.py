"""TurnRunner + InProcessBroker: background turns, WS-as-subscriber (design §4)."""

import asyncio
import uuid

import pytest

from app.domain.agent.runtime import InProcessBroker, TurnRunner


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
        lambda _chat, tid, after, why: scheduled.append((tid, after, why)),
    )

    class _Chat:
        async def post_system_event(self, topic_id, text, turn_id=None):
            return {"id": "b1", "content": text}

    n = await runner.resume_orphans(_Chat())
    assert n == 1
    assert [s[0] for s in scheduled] == [topic]
    # registry cleared: a second sweep is a no-op
    assert await runner.resume_orphans(_Chat()) == 0


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
