"""AgentWorkRunner + InProcessBroker: background turns, WS-as-subscriber (design §4)."""

import asyncio
import errno
import time
import uuid

import pytest
from sqlalchemy import select

from app.domain.agent.models import AgentTurn
from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker
from app.domain.identity.actor import Actor
from tests.turn_log import a_topic, open_turn, open_turn_ids, turn_row


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
    that it ran even if no one consumes the result.

    `session_factory` is where the runner keeps its turn intervals — a real
    ChatService carries one, so a stand-in that runs turns has to as well."""

    def __init__(self, frames, session_factory=None):
        self._frames = frames
        self.session_factory = session_factory
        self.ran = False
        self.kwargs: dict = {}

    async def converse(self, **kwargs):
        self.ran = True
        self.kwargs = kwargs
        for f in self._frames:
            await asyncio.sleep(0)  # yield control, like a real streaming turn
            yield f


async def _next_frame(
    queue: asyncio.Queue[dict], kind: str, *, timeout: float = 1.0
) -> dict:
    """Read through lifecycle/control frames until the requested frame lands."""
    async with asyncio.timeout(timeout):
        while True:
            frame = await queue.get()
            if frame["type"] == kind:
                return frame


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
    await broker.publish("c", {"type": "turn_started", "turn_id": "t1"})
    await broker.publish("c", {"type": "user_block"})
    await broker.publish("c", {"type": "delta", "text": "a"})
    async with broker.subscribe("c", replay=True) as q:  # joins mid-turn
        await broker.publish("c", {"type": "delta", "text": "b"})
        got = [q.get_nowait()["type"] for _ in range(4)]
    assert got == ["turn_started", "user_block", "delta", "delta"]


@pytest.mark.anyio
async def test_buffer_drops_after_turn_so_fresh_subscriber_replays_nothing():
    # Between turns the buffer is empty (the result is persisted as blocks), so a
    # subscriber that connects to start a new turn doesn't replay the dead one.
    broker = InProcessBroker()
    await broker.publish("c", {"type": "turn_started", "turn_id": "t1"})
    await broker.publish("c", {"type": "user_block"})
    await broker.publish("c", {"type": "done"})
    assert broker.in_flight("c") is True  # request done != turn done
    await broker.publish("c", {"type": "turn_finished", "turn_id": "t1"})
    async with broker.subscribe("c", replay=True) as q:
        assert q.empty()
    assert broker._buffer == {}


@pytest.mark.anyio
async def test_runner_publishes_turn_frames_to_subscribers(db_factory):
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    chat = _FakeChat(
        [{"type": "user_block"}, {"type": "delta", "text": "x"}, {"type": "done"}],
        db_factory,
    )
    topic = await a_topic(db_factory)
    async with broker.subscribe(str(topic)) as q:
        runner.submit(chat, topic, author="u", content="hi", summon=True)
        seen = []
        while True:
            f = await asyncio.wait_for(q.get(), 1)
            seen.append(f["type"])
            if f["type"] == "turn_finished":
                break
    assert seen == [
        "turn_started",
        "user_block",
        "delta",
        "done",
        "turn_finished",
    ]


@pytest.mark.anyio
async def test_a_turn_the_session_did_not_adopt_is_still_reported_finished(db_factory):
    """开了标记就得有人关掉它——哪怕会话没有接手这一轮。

    `session_lifecycle` says a live session will own this turn's ending. It does
    not say whose ending: when a turn begins on a topic that already has one
    running, the session reuses the activity it is already holding instead of
    opening a second, so the newer turn never appears on its books and never
    comes off them.

    Nothing else publishes `turn_finished`, so the mark then outlives the turn
    by the length of the process — the room keeps 正在思考 and the drain count
    never returns to zero (#604). The turn is lost either way; the topic must
    not be.
    """
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)

    class _SessionBusyWithAnEarlierTurn(_FakeChat):
        def session_took_over(self, topic_id, turn_id):
            del topic_id, turn_id
            return False

    chat = _SessionBusyWithAnEarlierTurn(
        # The order production emits: the room acknowledges the request first,
        # and only then does the handover frame arrive — which is exactly how
        # the runner comes to hold a mark it has decided not to close.
        [{"type": "user_block"}, {"type": "session_lifecycle"}, {"type": "done"}],
        db_factory,
    )
    topic = await a_topic(db_factory)
    async with broker.subscribe(str(topic)) as q:
        runner.submit(chat, topic, author="u", content="hi", summon=True)
        await _next_frame(q, "done")

    for _ in range(200):
        if not broker.active_turn_ids(str(topic)):
            break
        await asyncio.sleep(0.01)
    assert broker.active_turn_ids(str(topic)) == [], (
        "这一轮开了标记却没人关，话题会一直被报成在忙"
    )


@pytest.mark.anyio
async def test_cloud_wait_is_terminal_without_spending_a_retry(db_factory):
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    chat = _FakeChat(
        [
            {"type": "waiting", "state": "cloud_provisioning"},
            {"type": "done"},
        ],
        db_factory,
    )
    actor = Actor("owner", 1, False, "token")

    runner.submit(
        chat,
        await a_topic(db_factory),
        author="owner",
        content="保留这条消息",
        summon=True,
        provision_actor=actor,
    )
    async with asyncio.timeout(1):
        while not chat.ran or runner.active_work_count():
            await asyncio.sleep(0.01)

    assert runner.recent_work()[0]["status"] == "waiting"
    assert chat.kwargs["provision_actor"] is actor
    # A cloud wait is terminal: no second turn is spun up behind it.
    assert len(runner.recent_work()) == 1


class _FakeKickoffChat:
    """Stand-in ChatService.kickoff: the 分身's first turn after a split —
    frames flow with NO human message posted."""

    def __init__(self, session_factory=None):
        self.session_factory = session_factory
        self.ran = False

    async def kickoff(self, *, topic_id, turn_id=None, prompt=None):
        self.ran = True
        self.prompt = prompt
        await asyncio.sleep(0)
        yield {"type": "delta", "text": "开场白"}
        yield {"type": "done"}


@pytest.mark.anyio
async def test_submit_kickoff_runs_first_turn_without_user_block(db_factory):
    # 分身自动开工 (spec §8.4): a split sub-topic's first turn starts by itself;
    # the frame stream carries the 分身's own opening, never a user_block.
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    chat = _FakeKickoffChat(db_factory)
    topic = await a_topic(db_factory)
    async with broker.subscribe(str(topic)) as q:
        runner.submit_kickoff(chat, topic)
        seen = []
        while True:
            f = await asyncio.wait_for(q.get(), 1)
            seen.append(f["type"])
            if f["type"] == "done":
                break
    assert chat.ran is True
    assert seen == ["turn_started", "delta", "done"]
    assert "user_block" not in seen


@pytest.mark.anyio
async def test_turn_runs_to_completion_without_a_subscriber(db_factory):
    # The job does not depend on who is watching (invariant 2): no subscriber,
    # the turn still runs.
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    chat = _FakeChat([{"type": "done"}], db_factory)
    runner.submit(
        chat, await a_topic(db_factory), author="u", content="hi", summon=True
    )
    for _ in range(200):
        await asyncio.sleep(0.01)
        if chat.ran:
            break
    assert chat.ran is True


@pytest.mark.anyio
async def test_wedged_turn_times_out_and_is_cancelled(db_factory):
    # R8: a turn that never finishes must not hold on forever. With a tiny budget
    # it is interrupted (error frame) and the converse generator is cancelled.
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker, turn_timeout_s=0.05)
    cancelled = asyncio.Event()

    class _Hang:
        session_factory = db_factory

        async def converse(self, **_):
            yield {"type": "user_block"}
            try:
                await asyncio.sleep(10)  # wedge
            except asyncio.CancelledError:
                cancelled.set()
                raise
            yield {"type": "done"}  # pragma: no cover

    topic = await a_topic(db_factory)
    async with broker.subscribe(str(topic)) as q:
        runner.submit(_Hang(), topic, author="u", content="hi", summon=True)
        kinds = []
        for _ in range(4):
            f = await asyncio.wait_for(q.get(), 1)
            kinds.append(f["type"])
            if f["type"] == "error":
                break
    assert kinds == ["turn_started", "user_block", "error"]
    await asyncio.wait_for(cancelled.wait(), 1)  # the wedged turn was cancelled


# --- turn 活跃度检测 (2026-08-09): `turn_ceiling` reschedules the outer wrap.
# hooks_substrate's two-layer idle-suspect/hard-ceiling logic is pointless if
# THIS outer, transport-independent wrap still kills the turn at the generic
# `agent_turn_timeout_s` regardless of activity — these prove the reschedule
# actually takes effect, is scoped to only the turn that asks for it, and never
# leaks as a visible frame to subscribers.


@pytest.mark.anyio
async def test_turn_ceiling_frame_reschedules_the_outer_timeout(db_factory):
    """The tmux backend signals its OWN (longer) ceiling via a `turn_ceiling`
    frame — AgentWorkRunner must reschedule its outer wall-clock wrap to that value."""
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker, turn_timeout_s=0.05)  # the generic default

    class _LongTmuxTurn:
        session_factory = db_factory

        async def converse(self, **_):
            yield {"type": "turn_ceiling", "seconds": 10.0}
            # Where the declared ceiling takes effect: both clocks have a real
            # base only once the prompt has landed, so this frame is what a real
            # `converse` sends between the two (chat.py sends it on every turn).
            yield {"type": "prompt_delivered"}
            # Longer than the generic 0.05s default, well under the 10s ceiling
            # this turn actually asked for.
            await asyncio.sleep(0.15)
            yield {"type": "done"}

    topic = await a_topic(db_factory)
    async with broker.subscribe(str(topic)) as q:
        runner.submit(_LongTmuxTurn(), topic, author="u", content="hi", summon=True)
        f = await _next_frame(q, "done", timeout=2)
    # Never timed out, and the internal control frame never leaked to subscribers.
    assert f["type"] == "done"


@pytest.mark.anyio
async def test_topic_turn_reports_the_rescheduled_ceiling(db_factory):
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker, turn_timeout_s=0.05)

    class _Turn:
        session_factory = db_factory

        async def converse(self, **_):
            yield {"type": "turn_ceiling", "seconds": 123.0}
            yield {"type": "done"}

    topic = await a_topic(db_factory)
    async with broker.subscribe(str(topic)) as q:
        runner.submit(_Turn(), topic, author="u", content="hi", summon=True)
        await _next_frame(q, "done", timeout=2)
    rec = runner.topic_work(topic)
    assert rec is not None
    assert rec["ceiling_s"] == 123


@pytest.mark.anyio
async def test_timeout_message_reports_the_effective_ceiling_and_elapsed(db_factory):
    """F: the timeline message a timed-out turn posts used to drop the actual
    timeout value entirely ("⚠️ 芝士这轮超时被中断了..." with no number) —
    only logger.warning had it, and agent has no host SSH to read logger. The
    message must carry the SAME effective ceiling `topic_work()`/`cheese
    status` report, plus roughly how long it actually ran."""
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker, turn_timeout_s=1.0)

    class _Hang:
        session_factory = db_factory

        def __init__(self) -> None:
            self.posted: str | None = None
            self.posted_meta: dict | None = None

        async def converse(self, **_):
            yield {"type": "user_block"}
            # 说过话之后才卡住。这一帧是必需的，不是装饰：它把这一轮明确地放进
            # 「跑起来了然后卡住」那一类，而不是「压根没起来」那一类
            # （见 test_cold_start_watchdog.py）。两类共用这个 except 分支、
            # 报的话术不同，而这条测试要钉的是前者那句。
            yield {"type": "assistant_block", "text": "在看了"}
            await asyncio.sleep(10)  # wedge, well past the 1s ceiling
            yield {"type": "done"}  # pragma: no cover

        async def post_system_event(self, topic_id, content, turn_id=None, meta=None):
            self.posted = content
            self.posted_meta = meta
            return {"id": "b1", "kind": "event", "content": content}

    svc = _Hang()
    topic = await a_topic(db_factory)
    async with broker.subscribe(str(topic)) as q:
        runner.submit(svc, topic, author="u", content="hi", summon=True)
        await _next_frame(q, "event_block", timeout=3)
    assert svc.posted is not None
    assert "1秒的上限" in svc.posted
    # 平台提示统一契约: 房间里一行，"实际跑了约 N 秒"收进 meta.detail 由前端折叠。
    assert svc.posted_meta is not None
    assert svc.posted_meta["event_type"] == "turn_timeout"
    assert "实际跑了约" in svc.posted_meta["detail"]


@pytest.mark.anyio
async def test_timeout_message_uses_the_rescheduled_ceiling_not_the_generic_default(
    db_factory,
):
    """A turn that rescheduled its ceiling via `turn_ceiling` (turn 活跃度检测,
    e.g. the tmux backend) must have its timeout message report THAT ceiling,
    not the generic outer default — otherwise "was this the generic safety
    net or the backend's real, much longer ceiling" is unanswerable without
    host SSH."""
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker, turn_timeout_s=0.05)  # tiny generic default

    class _Hang:
        session_factory = db_factory

        def __init__(self) -> None:
            self.posted: str | None = None
            self.posted_meta: dict | None = None

        async def converse(self, **_):
            yield {"type": "turn_ceiling", "seconds": 2.0}
            # 同上：`turn_ceiling` 只说明选中了哪个后端，不说明它起来了。要让这一轮
            # 真的按「后端自己的上限」跑完再超时，它得先开口——否则冷启动保险丝
            # 会先把它按「运行环境没起来」砍掉，报的就是另一句话。
            yield {"type": "assistant_block", "text": "在看了"}
            await asyncio.sleep(10)  # wedge, well past the rescheduled 2s
            yield {"type": "done"}  # pragma: no cover

        async def post_system_event(self, topic_id, content, turn_id=None, meta=None):
            self.posted = content
            self.posted_meta = meta
            return {"id": "b1", "kind": "event", "content": content}

    svc = _Hang()
    topic = await a_topic(db_factory)
    async with broker.subscribe(str(topic)) as q:
        runner.submit(svc, topic, author="u", content="hi", summon=True)
        await _next_frame(q, "event_block", timeout=5)
    assert svc.posted is not None
    assert "2秒的上限" in svc.posted
    assert "0.05" not in svc.posted


@pytest.mark.anyio
async def test_running_topic_ids_reports_only_in_flight_turns(db_factory):
    # Bulk signal for the sidebar's「芝士还在跑」indicator: a topic whose turn
    # already finished must drop out, one still mid-flight must show up.
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)

    class _SlowTurn:
        session_factory = db_factory

        async def converse(self, **_):
            await asyncio.sleep(0.2)
            yield {"type": "done"}

    finished_topic = await a_topic(db_factory)
    running_topic = await a_topic(db_factory)

    async with broker.subscribe(str(finished_topic)) as q:
        runner.submit(
            _FakeChat([{"type": "done"}], db_factory),
            finished_topic,
            author="u",
            content="hi",
            summon=True,
        )
        await _next_frame(q, "turn_finished")

    runner.submit(_SlowTurn(), running_topic, author="u", content="hi", summon=True)
    await asyncio.sleep(0.05)  # started, but its 0.2s sleep hasn't resolved yet

    ids = runner.running_topic_ids()
    assert running_topic in ids
    assert finished_topic not in ids


@pytest.mark.anyio
async def test_runner_publishes_friendly_error_on_failure(db_factory):
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)

    class _Boom:
        session_factory = db_factory

        async def converse(self, **_):
            raise RuntimeError("kaboom")
            yield  # pragma: no cover — makes this an async generator

    topic = await a_topic(db_factory)
    async with broker.subscribe(str(topic)) as q:
        runner.submit(_Boom(), topic, author="u", content="hi", summon=True)
        frame = await _next_frame(q, "error")
    assert frame["type"] == "error"


@pytest.mark.anyio
async def test_turn_failure_lands_in_the_timeline(db_factory):
    """现场即事实记录: a failed turn persists a system event block (survives
    reload, scrolls with the flow) and marks the error frame persisted=True so
    the client doesn't double-show a banner."""
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)

    class _Boom:
        session_factory = db_factory

        def __init__(self) -> None:
            self.posted: str | None = None
            self.posted_meta: dict | None = None

        async def converse(self, **_):
            raise RuntimeError("kaboom")
            yield  # pragma: no cover — makes this an async generator

        async def post_system_event(self, topic_id, content, turn_id=None, meta=None):
            self.posted = content
            self.posted_meta = meta
            return {"id": "b1", "kind": "event", "content": content}

    svc = _Boom()
    topic = await a_topic(db_factory)
    async with broker.subscribe(str(topic)) as q:
        runner.submit(svc, topic, author="u", content="hi", summon=True)
        first = await _next_frame(q, "event_block")
        second = await _next_frame(q, "error")
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
                "screen setup failed: Unable to find image "
                "'cheesex-agent-sandbox:latest' locally: pull access denied"
            ),
            "runtime_image_missing",
        ),
    ],
)
async def test_platform_failure_is_coded_and_never_auto_resumes(
    db_factory, failure, expected_code
):
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)

    class _Full:
        session_factory = db_factory

        def __init__(self) -> None:
            self.meta: dict | None = None
            self.converse_calls = 0

        async def converse(self, **_):
            self.converse_calls += 1
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

    svc = _Full()
    topic = await a_topic(db_factory)
    async with broker.subscribe(str(topic)) as q:
        runner.submit(svc, topic, author="u", content="hi", summon=True)
        event = await _next_frame(q, "event_block")
        error = await _next_frame(q, "error")

    assert event["type"] == "event_block"
    assert event["block"]["meta"]["code"] == expected_code
    assert error == {
        "type": "error",
        "code": expected_code,
        "message": event["block"]["content"],
        "persisted": True,
    }
    assert svc.meta == event["block"]["meta"]
    # A named platform incident waits for recovery; it never re-runs the turn.
    await asyncio.sleep(0.05)
    assert svc.converse_calls == 1


@pytest.mark.anyio
async def test_failed_turn_fails_loud_and_does_not_resume(db_factory):
    """An unnamed crash is a bug signal, not a transience signal: the turn posts
    ONE event handing the topic to a person and is NOT re-run — retrying a bug
    just triggers it again (the 2026-09-04 room flood)."""
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)

    class _Svc:
        session_factory = db_factory

        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.events: list[tuple[str, dict]] = []

        async def converse(self, **kw):
            self.calls.append(kw)
            raise RuntimeError("boom")
            yield  # pragma: no cover — makes this an async generator

        async def post_system_event(self, topic_id, content, turn_id=None, meta=None):
            self.events.append((content, meta or {}))
            return {"id": "sys", "kind": "event", "content": content, "meta": meta}

    svc = _Svc()
    topic = await a_topic(db_factory)
    async with broker.subscribe(str(topic)) as q:
        runner.submit(svc, topic, author="u", content="hi", summon=True)
        error = await _next_frame(q, "error")

    # Give any (erroneously) scheduled follow-up turn a chance to fire.
    await asyncio.sleep(0.05)
    assert len(svc.calls) == 1, "a crashed turn must not be re-run"
    text, meta = svc.events[0]
    assert error["message"] == text
    # Handed to a person, and it does not promise the platform will self-recover.
    assert meta["who"] == "human"
    assert "自动恢复" not in ((meta.get("detail") or "") + text)


@pytest.mark.anyio
async def test_a_resent_turn_that_crashes_also_fails_loud(db_factory):
    """A re-sent turn (is_resume) that then crashes is handled exactly like any
    other unnamed failure — one event to a person, no further automatic turn.
    is_resume no longer buys a bounded retry chain; there is no chain."""
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)

    class _BoomAgain:
        session_factory = db_factory

        def __init__(self) -> None:
            self.calls = 0
            self.events: list[tuple[str, dict]] = []

        async def converse(self, **_):
            self.calls += 1
            raise RuntimeError("still broken")
            yield  # pragma: no cover

        async def post_system_event(self, topic_id, content, turn_id=None, meta=None):
            self.events.append((content, meta or {}))
            return {"id": "sys", "kind": "event", "content": content, "meta": meta}

    svc = _BoomAgain()
    topic = await a_topic(db_factory)
    async with broker.subscribe(str(topic)) as queue:
        runner.submit(
            svc,
            topic,
            author="system",
            content="continue",
            summon=True,
            is_resume=True,
        )
        error = await _next_frame(queue, "error")

    await asyncio.sleep(0.05)
    assert svc.calls == 1, "a crashed re-sent turn must not chain another turn"
    text, meta = svc.events[-1]
    assert meta["who"] == "human"
    assert "自动恢复" not in ((meta.get("detail") or "") + text)
    assert error["message"] == text


@pytest.mark.anyio
async def test_orphan_turns_resume_after_restart(db_factory, monkeypatch):
    """A turn that was RUNNING when the process died must be swept up on the
    next startup: ⚠️ event posted + (for an undelivered human prompt) a re-send
    of the original message — never silently vanish (the deploy-kills-a-turn
    hole). Delivered turns take the attach path instead — see
    test_orphan_sweep_attach.py."""
    topic = await a_topic(db_factory)
    await open_turn(db_factory, topic, content="修一下登录页", age_s=60)
    # a re-sent turn (is_resume, not resendable) must never itself trigger
    # another automatic turn across a restart: it is a re-delivery, not work a
    # task-less session can pick up, so it is dropped loudly instead.
    await open_turn(
        db_factory,
        await a_topic(db_factory),
        author="system",
        content="重发的一轮",
        age_s=60,
        is_resume=True,
        resendable=False,
    )
    # stale (>2h) intervals are dropped, not resurrected
    await open_turn(db_factory, await a_topic(db_factory), content="旧任务", age_s=7300)

    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    scheduled: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(
        runner,
        "_schedule_resend",
        lambda _chat, tid, after, content, **_kw: scheduled.append((tid, content)),
    )

    class _Chat:
        def __init__(self):
            self.events: list[tuple[uuid.UUID, str]] = []
            self.notices: list[tuple[uuid.UUID, str]] = []

        async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
            self.events.append((topic_id, text))
            # 平台提示统一契约: 房间里的一行是 `text`，展开才看的长文在
            # `meta.detail` —— 想断言"提示里说了什么"就得把两半都算上。
            self.notices.append((topic_id, text + ((meta or {}).get("detail") or "")))
            return {"id": "b1", "content": text}

        def has_live_screen(self, topic_id):
            return False  # the container went with the deploy

        async def turns_that_produced_something(self, turn_ids):
            return set()

        def schedule_spool_settle(self, topic_id, delay_s=2.0):
            raise AssertionError("no evidence → nothing to attach to")

    chat = _Chat()
    chat.session_factory = db_factory
    n = await runner.resume_orphans(chat)
    assert n == 1
    # The undelivered human turn is re-sent with its ORIGINAL text — never a
    # "接着干" nudge a claude that heard nothing could act on.
    assert scheduled == [(topic, "修一下登录页")]
    # Two, not three. The re-sent turn says nothing: the platform is handling it
    # and there is no anomaly for the room to explain. The other two are genuinely
    # stranded — nothing will re-send them and nothing else in the room shows it —
    # so each asks a human to step in.
    assert len(chat.events) == 2
    dropped = [text for tid, text in chat.notices if tid != topic]
    assert len(dropped) == 2
    assert all("@ 芝士" in text for text in dropped)
    # every interval closed: a second sweep is a no-op
    second = _Chat()
    second.session_factory = db_factory
    assert await runner.resume_orphans(second) == 0


@pytest.mark.anyio
async def test_periodic_sweep_claims_turn_killed_without_a_restart(
    db_factory, monkeypatch
):
    """The 101/173-minute hole: a turn can be killed (container recreate, OOM,
    sandbox swap) while the PROCESS lives on. Nothing then re-reads the open
    intervals on the startup path, so the sweep must also run periodically — and
    it must tell the live turns apart from the corpses."""
    dead_topic = await a_topic(db_factory)
    live_topic = await a_topic(db_factory)
    dead = await open_turn(db_factory, dead_topic, content="查一下日志", age_s=600)
    live = await open_turn(db_factory, live_topic, content="别动我", age_s=600)

    runner = AgentWorkRunner(InProcessBroker())
    task = await _park_a_task()  # this process really is running it
    runner._live[str(live)] = task
    runner._last_frame_at[str(live)] = time.monotonic()
    scheduled: list[uuid.UUID] = []
    monkeypatch.setattr(
        runner,
        "_schedule_resend",
        lambda _chat, tid, after, content, **_kw: scheduled.append(tid),
    )

    class _Chat:
        session_factory = db_factory

        async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
            return {"id": "b1", "content": text}

        def has_live_screen(self, topic_id):
            return False  # the container went with the deploy

        async def turns_that_produced_something(self, turn_ids):
            return set()

    assert await runner.sweep_orphans(_Chat()) == 1
    assert scheduled == [dead_topic]
    # The live turn's interval stays open — its own completion path closes it;
    # the dead one is claimed, so a second sweep does not double-resume it.
    assert await open_turn_ids(db_factory) == {live}
    assert dead is not None
    assert await runner.sweep_orphans(_Chat()) == 0


@pytest.mark.anyio
async def test_periodic_sweep_ignores_a_just_started_turn(db_factory):
    """Belt-and-braces against resuming a live turn: an interval younger than
    SWEEP_MIN_AGE_S is never claimed, even if `_live` somehow missed it."""
    fresh = await open_turn(db_factory, await a_topic(db_factory), age_s=2)
    runner = AgentWorkRunner(InProcessBroker())

    class _Chat:
        session_factory = db_factory

        async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
            raise AssertionError("a just-started turn must not be touched")

    assert await runner.sweep_orphans(_Chat()) == 0
    assert await open_turn_ids(db_factory) == {fresh}


@pytest.mark.anyio
async def test_stale_orphan_is_dropped_loudly(db_factory, monkeypatch):
    """Crossing the 2h line must not be a silent `continue`. We still don't
    auto-resume (that part was right) — but the topic has to say so, because a
    human is now the only thing that can move it."""
    topic = await a_topic(db_factory)
    # 173 min, the real incident
    await open_turn(db_factory, topic, content="老任务", age_s=10380)
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    seen: list[dict] = []

    async def _capture(channel, frame):
        seen.append(frame)

    monkeypatch.setattr(broker, "publish", _capture)

    class _Chat:
        def __init__(self):
            self.texts: list[str] = []
            # 平台提示统一契约: 房间里的一行是 `text`，展开才看的长文在
            # `meta.detail` —— 断言"提示里说了什么"要把两半都算上。
            self.notices: list[str] = []

        async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
            self.texts.append(text)
            self.notices.append(text + ((meta or {}).get("detail") or ""))
            return {"id": "b1", "content": text}

        def has_live_screen(self, topic_id):
            return False  # the container went with the deploy

        async def turns_that_produced_something(self, turn_ids):
            return set()

    chat = _Chat()
    chat.session_factory = db_factory
    assert await runner.sweep_orphans(chat) == 0  # not resumed...
    assert len(chat.texts) == 1  # ...but not silent either
    assert "173" in chat.texts[0]  # says how long it has been dead
    assert "@ 芝士" in chat.notices[0]  # says what the human can do
    # claimed (the interval is closed), so it is not re-announced
    assert await open_turn_ids(db_factory) == set()
    assert [f["type"] for f in seen] == ["event_block"]  # pushed to the UI live


@pytest.mark.anyio
async def test_a_finished_turn_leaves_a_closed_interval(db_factory):
    """While a turn runs its interval is open; when it ends the interval closes
    — so only genuinely orphaned turns survive to the next startup, and the turn
    that DID finish is still there to be asked about."""
    topic = await a_topic(db_factory)
    runner = AgentWorkRunner(InProcessBroker())
    chat = _FakeChat([{"type": "done"}], db_factory)
    runner.submit(chat, topic, author="u", content="hi", summon=True)
    for _ in range(200):
        await asyncio.sleep(0.01)
        if chat.ran and not await open_turn_ids(db_factory):
            break
    assert chat.ran
    assert await open_turn_ids(db_factory) == set()
    # Closed, not erased: this id is on every block the turn produced.
    async with db_factory() as session:
        rows = list((await session.execute(select(AgentTurn))).scalars())
    assert len(rows) == 1
    assert rows[0].topic_id == topic
    assert rows[0].stopped_at is not None


@pytest.mark.anyio
async def test_in_flight_reflects_replay_buffer():
    """Only explicit lifecycle markers mutate in_flight state."""
    broker = InProcessBroker()
    assert broker.in_flight("t") is False
    await broker.publish("t", {"type": "event_block", "block": {}})
    assert broker.in_flight("t") is False  # idle system event
    await broker.publish("t", {"type": "turn_started", "turn_id": "one"})
    assert broker.in_flight("t") is True
    await broker.publish("t", {"type": "done"})
    assert broker.in_flight("t") is True  # request completion is not lifecycle
    await broker.publish("t", {"type": "turn_finished", "turn_id": "one"})
    assert broker.in_flight("t") is False


@pytest.mark.anyio
async def test_finishing_one_turn_does_not_clear_another_turns_state():
    broker = InProcessBroker()
    await broker.publish("t", {"type": "turn_started", "turn_id": "one"})
    await broker.publish("t", {"type": "turn_started", "turn_id": "two"})
    await broker.publish("t", {"type": "turn_finished", "turn_id": "one"})
    assert broker.in_flight("t") is True
    assert broker.active_turn_ids("t") == ["two"]
    await broker.publish("t", {"type": "turn_finished", "turn_id": "two"})
    assert broker.in_flight("t") is False


@pytest.mark.anyio
async def test_sweep_claims_a_turn_that_is_live_but_silent(db_factory):
    """The 8-hour incident (2026-08-11): three topics went quiet after their last
    block and nothing noticed all night, while this process was up the whole
    time. The task was still in `_live` — what died was the container it drove —
    so `open intervals - _live` alone sweeps right past it. Silence is the judge."""
    from datetime import UTC, datetime, timedelta

    topic = await a_topic(db_factory)
    wedged = await open_turn(db_factory, topic, age_s=8 * 3600)
    runner = AgentWorkRunner(InProcessBroker())
    task = await _park_a_task()
    runner._live[str(wedged)] = task
    # Both signals cold: no frame for 8h, and the topic's newest block is 8h old.
    runner._last_frame_at[str(wedged)] = time.monotonic() - 8 * 3600

    async def _last_block(topic_ids):
        assert topic_ids == {topic}
        return {topic: datetime.now(UTC) - timedelta(hours=8)}

    class _Chat:
        def __init__(self):
            self.texts: list[str] = []
            # 平台提示统一契约: 房间里的一行是 `text`，展开才看的长文在
            # `meta.detail` —— 断言"提示里说了什么"要把两半都算上。
            self.notices: list[str] = []

        async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
            self.texts.append(text)
            self.notices.append(text + ((meta or {}).get("detail") or ""))
            return {"id": "b1", "content": text}

    chat = _Chat()
    chat.session_factory = db_factory
    # 8h is past ORPHAN_STALE_S, so it is not auto-resumed — but it must still be
    # torn down and announced, which is the entire point.
    assert await runner.sweep_orphans(chat, last_activity=_last_block) == 0
    await asyncio.sleep(0)
    assert task.cancelled() or task.cancelling()  # the zombie no longer holds the lock
    assert len(chat.texts) == 1
    assert "卡死" in chat.texts[0]  # says HOW it died, not just that it did
    assert "@ 芝士" in chat.notices[0]
    assert await open_turn_ids(db_factory) == set()


@pytest.mark.anyio
async def test_sweep_spares_a_turn_grinding_through_tools(db_factory):
    """A tool call persists no Block, so a turn deep in a tool chain can look
    silent to the DB while being perfectly alive. Cancelling that is worse than
    catching a corpse late — the frame signal is what prevents it."""
    from datetime import UTC, datetime, timedelta

    topic = await a_topic(db_factory)
    busy = await open_turn(db_factory, topic, age_s=4 * 3600)
    runner = AgentWorkRunner(InProcessBroker())
    task = await _park_a_task()
    runner._live[str(busy)] = task
    runner._last_frame_at[str(busy)] = time.monotonic() - 5  # a tool frame just now

    async def _last_block(topic_ids):
        return {topic: datetime.now(UTC) - timedelta(hours=4)}  # DB says silent

    class _Chat:
        session_factory = db_factory

        async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
            raise AssertionError("a turn that is still emitting frames is alive")

    assert await runner.sweep_orphans(_Chat(), last_activity=_last_block) == 0
    assert not task.cancelled()
    assert await open_turn_ids(db_factory) == {busy}  # untouched
    task.cancel()


@pytest.mark.anyio
async def test_sweep_spares_live_turns_when_the_activity_probe_fails(db_factory):
    """A DB hiccup must not become a mass cancellation: with no usable evidence
    of silence, a live turn is assumed alive."""
    live = await open_turn(db_factory, await a_topic(db_factory), age_s=9 * 3600)
    runner = AgentWorkRunner(InProcessBroker())
    task = await _park_a_task()
    runner._live[str(live)] = task
    runner._last_frame_at[str(live)] = time.monotonic() - 9 * 3600

    async def _boom(topic_ids):
        raise RuntimeError("PG is down")

    class _Chat:
        session_factory = db_factory

        async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
            raise AssertionError("no verdict may be reached without evidence")

    assert await runner.sweep_orphans(_Chat(), last_activity=_boom) == 0
    assert not task.cancelled()
    assert await open_turn_ids(db_factory) == {live}
    task.cancel()


@pytest.mark.anyio
async def test_a_wedged_turn_is_cancelled_and_handed_to_a_human(db_factory):
    """A wedged turn is torn down and announced, but NOT re-run: re-running only
    re-enters the machine that just died under it, so a person picks it up and
    re-@s 芝士 once the environment is back."""
    from datetime import UTC, datetime, timedelta

    topic = await a_topic(db_factory)
    # 45 min: silent, but not stale
    wedged = await open_turn(db_factory, topic, age_s=2700)
    runner = AgentWorkRunner(InProcessBroker())
    task = await _park_a_task()
    runner._live[str(wedged)] = task
    runner._last_frame_at[str(wedged)] = time.monotonic() - 2700

    async def _last_block(topic_ids):
        return {topic: datetime.now(UTC) - timedelta(seconds=2700)}

    class _Chat:
        session_factory = db_factory

        def __init__(self) -> None:
            self.metas: list[dict] = []
            # 平台提示统一契约: 房间里的一行是 text，长文在 meta.detail。
            self.notices: list[str] = []

        async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
            self.metas.append(meta or {})
            self.notices.append(text + ((meta or {}).get("detail") or ""))
            return {"id": "b1", "content": text}

    chat = _Chat()
    # Zero remedial re-sends scheduled: a wedged turn is cancelled and announced,
    # never re-run.
    assert await runner.sweep_orphans(chat, last_activity=_last_block) == 0
    await asyncio.sleep(0)
    assert task.cancelled() or task.cancelling()
    # Exactly one notice, and it hands the topic to a person — no auto-retry.
    assert len(chat.metas) == 1
    assert chat.metas[0].get("who") == "human"
    assert "@ 芝士" in chat.notices[0]


@pytest.mark.anyio
async def test_sweep_keeps_a_turn_that_opened_while_it_was_probing(db_factory):
    """The sweep must not close intervals it never looked at.

    It reads the open intervals, then AWAITS the activity probe, then claims. A
    turn that starts inside that window opens its own interval — and claiming
    "everything that was open" would close it. The turn keeps running with a
    closed interval, so no later sweep can ever find it: its death would be
    silent forever, which is the exact failure this whole mechanism exists to
    end.
    """
    from datetime import UTC, datetime, timedelta

    dead_topic = await a_topic(db_factory)
    new_topic = await a_topic(db_factory)
    wedged = await open_turn(db_factory, dead_topic, age_s=8 * 3600)
    runner = AgentWorkRunner(InProcessBroker())
    task = await _park_a_task()
    runner._live[str(wedged)] = task
    runner._last_frame_at[str(wedged)] = time.monotonic() - 8 * 3600
    newcomer: list[uuid.UUID] = []

    async def _last_block(topic_ids):
        # A fresh turn starts while the probe is in flight, exactly as a real
        # `_execute` would.
        newcomer.append(await open_turn(db_factory, new_topic, age_s=0))
        return {dead_topic: datetime.now(UTC) - timedelta(hours=8)}

    class _Chat:
        session_factory = db_factory

        async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
            return {"id": "b1", "content": text}

    await runner.sweep_orphans(_Chat(), last_activity=_last_block)

    left = await open_turn_ids(db_factory)
    assert wedged not in left  # claimed and announced
    assert newcomer[0] in left  # never judged, so never closed
    task.cancel()


@pytest.mark.anyio
async def test_live_turn_for_topic_tracks_a_running_turn(db_factory):
    """The heartbeat half of the stall verdict (`/topics/{id}/status` → `stall`).

    It has to answer from what the process is REALLY executing, not from
    `_recent`: that ring buffer records what turns did, so it keeps saying
    `running` about a turn the process died holding. Here: nothing before the
    turn, a live entry with a fresh frame stamp while it streams, nothing again
    once it ends.
    """
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    topic = await a_topic(db_factory)
    assert runner.live_work_for_topic(topic) is None

    streaming = asyncio.Event()
    finish = asyncio.Event()

    class _Slow:
        session_factory = db_factory

        async def converse(self, **_):
            yield {"type": "user_block"}
            streaming.set()
            await finish.wait()
            yield {"type": "done"}

    turn_id = runner.submit(_Slow(), topic, author="u", content="hi", summon=True)
    await asyncio.wait_for(streaming.wait(), 1)
    live = runner.live_work_for_topic(topic)
    assert live is not None
    assert live["turn_id"] == str(turn_id)
    assert live["silent_for_s"] < 5  # a frame just went out
    assert runner.live_work_for_topic(uuid.uuid4()) is None  # scoped to its topic

    finish.set()
    for _ in range(200):
        await asyncio.sleep(0.01)
        if runner.live_work_for_topic(topic) is None:
            break
    assert runner.live_work_for_topic(topic) is None


@pytest.mark.anyio
async def test_a_killed_turn_stops_claiming_to_be_running(db_factory):
    """Tearing a wedged turn down must also END it for every reader.

    The sweep kills a wedged turn with `task.cancel()`. CancelledError is a
    BaseException, so it misses every `except` in `_execute`: the lifecycle
    record stayed `running` forever, and that record is exactly what
    `GET /topics` (`running`) and `/topics/{id}/status` (`turn`) report. The
    platform went on saying 芝士 was working on a turn it had just killed, and
    the broker's replay buffer kept greeting every reconnect with `turn_active`
    — the 8-hour incident's symptom outliving its own fix.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    broker = InProcessBroker()
    runner = AgentWorkRunner(broker, turn_timeout_s=300)
    topic = await a_topic(db_factory)
    streaming = asyncio.Event()

    class _DeadContainer:
        """A turn whose sandbox died mid-stream: frames stop, the task lives."""

        session_factory = db_factory

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
    async with db_factory() as session:
        await session.execute(
            update(AgentTurn)
            .where(AgentTurn.id == uuid.UUID(turn_id))
            .values(started_at=datetime.now(UTC) - timedelta(hours=8))
        )
        await session.commit()

    async def _last_block(topic_ids):
        return {topic: datetime.now(UTC) - timedelta(hours=8)}

    class _Chat:
        session_factory = db_factory

        async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
            return {"id": "b1", "content": text}

    await runner.sweep_orphans(_Chat(), last_activity=_last_block)
    for _ in range(200):  # let the cancellation land at the turn's await point
        await asyncio.sleep(0.01)
        if topic not in runner.running_topic_ids():
            break

    assert topic not in runner.running_topic_ids()
    assert runner.topic_work(topic)["status"] != "running"
    # A reconnecting client must not be told the dead turn is still streaming.
    assert broker.in_flight(str(topic)) is False


@pytest.mark.anyio
async def test_a_deploy_the_platform_handles_itself_says_nothing(
    db_factory, monkeypatch
):
    """Nothing happened that a person can see, so nothing is said.

    #316 added this event because a deploy left the room looking dead: the
    backend half died, nothing reattached, and the session's output only
    surfaced later out of the spool. Retiring the turn (#508) removed that —
    the subscription lives with the screen and reattaches on restart, so the
    room just keeps showing 芝士 working. What is left here is a message the
    platform re-sends down that same session, which is indistinguishable from
    the person asking again.
    """
    topic = await a_topic(db_factory)
    await open_turn(db_factory, topic, content="把测试跑一遍", age_s=60)

    runner = AgentWorkRunner(InProcessBroker())
    scheduled: list[uuid.UUID] = []
    monkeypatch.setattr(
        runner,
        "_schedule_resend",
        lambda _chat, tid, after, content, **_kw: scheduled.append(tid),
    )
    metas: list[dict] = []

    class _Chat:
        session_factory = db_factory

        async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
            metas.append(meta or {})
            return {"id": "b1", "content": text}

        def has_live_screen(self, topic_id):
            return False  # the container went with the deploy

        async def turns_that_produced_something(self, turn_ids):
            return set()

    assert await runner.sweep_orphans(_Chat()) == 1
    assert scheduled == [topic], "the re-send must still happen"
    assert metas == [], "nothing broke that a person can see"


@pytest.mark.anyio
async def test_a_delivered_prompt_is_recorded_before_the_process_can_die(db_factory):
    """The `prompt_delivered` frame must reach the durable interval.

    This wiring has no other symptom. If the frame stopped being emitted or
    stopped being handled, every turn would quietly go back to being judged by
    whether 芝士 happened to produce a block first — visible only later, as
    duplicate re-sends after a deploy. So the stamp is read WHILE the turn is
    still running, the way a dying process would leave it."""
    stamped = asyncio.Event()
    release = asyncio.Event()

    class _GatedChat:
        """Yields the delivery frame, then holds the turn open."""

        session_factory = db_factory

        async def converse(self, **kwargs):
            yield {"type": "prompt_delivered"}
            await asyncio.sleep(0)
            stamped.set()
            await release.wait()
            yield {"type": "done"}

    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    topic = await a_topic(db_factory)
    seen: list[str] = []

    async with broker.subscribe(str(topic)) as q:
        turn_id = runner.submit(
            _GatedChat(), topic, author="u", content="hi", summon=True
        )
        await asyncio.wait_for(stamped.wait(), 1)
        for _ in range(200):
            row = await turn_row(db_factory, turn_id)
            if row is not None and row.delivered_at is not None:
                break
            await asyncio.sleep(0.01)
        assert row is not None and row.delivered_at is not None
        release.set()
        while True:
            frame = await asyncio.wait_for(q.get(), 1)
            seen.append(frame["type"])
            if frame["type"] == "turn_finished":
                break

    # Internal frame: the runtime consumes it, the room never sees it.
    assert seen == ["turn_started", "done", "turn_finished"]


@pytest.mark.anyio
async def test_the_platforms_own_work_is_re_sent_like_anyone_elses(
    db_factory, monkeypatch
):
    """A turn the PLATFORM started — 验收卡被驳回, CI 红了, 上游合并冲突, a 分身's
    kickoff — is re-sent exactly like a person's message.

    The sweep used to skip these on the grounds that "no human message is in it
    to lose". Nothing is lost only in the sense that nobody typed it: the work
    itself still evaporates, the room shows nothing, and the topic sits until
    someone happens to notice. Whose turn it was never made it any less gone."""
    topic = await a_topic(db_factory)
    nudge = "PR #123 的检查没通过：…请在这个话题的工作区里修复问题并提交。"
    await open_turn(db_factory, topic, author="system", content=nudge, age_s=60)

    runner = AgentWorkRunner(InProcessBroker())
    scheduled: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(
        runner,
        "_schedule_resend",
        lambda _chat, tid, after, content, **_kw: scheduled.append((tid, content)),
    )
    metas: list[dict] = []

    class _Chat:
        session_factory = db_factory

        async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
            metas.append(meta or {})
            return {"id": "b1", "content": text}

        def has_live_screen(self, topic_id):
            return False  # the container went with the deploy

        async def turns_that_produced_something(self, turn_ids):
            return set()

    assert await runner.sweep_orphans(_Chat()) == 1
    assert scheduled == [(topic, nudge)]
    # And it says nothing, for the same reason a re-sent human message does: the
    # platform is handling it, so there is no anomaly to narrate.
    assert metas == []


@pytest.mark.anyio
async def test_a_deploy_that_loses_a_message_for_good_still_warns(
    db_factory, monkeypatch
):
    """The one case that survives: the message never reached 芝士 and the
    platform will not re-send it. Nothing else in the room shows that — the
    person would wait for an answer that is never coming."""
    topic = await a_topic(db_factory)
    await open_turn(
        db_factory,
        topic,
        content="把测试跑一遍",
        # Older than ORPHAN_STALE_S: too old to re-send on its own.
        age_s=AgentWorkRunner.ORPHAN_STALE_S + 600,
    )

    runner = AgentWorkRunner(InProcessBroker())
    monkeypatch.setattr(
        runner, "_schedule_resend", lambda *a, **k: pytest.fail("must not re-send")
    )
    metas: list[dict] = []

    class _Chat:
        session_factory = db_factory

        async def post_system_event(self, topic_id, text, turn_id=None, meta=None):
            metas.append(meta or {})
            return {"id": "b1", "content": text}

        def has_live_screen(self, topic_id):
            return False  # the container went with the deploy

        async def turns_that_produced_something(self, turn_ids):
            return set()

    assert await runner.sweep_orphans(_Chat()) == 0
    assert len(metas) == 1
    assert metas[0].get("severity") == "warn"
    assert metas[0].get("who") == "human"


@pytest.mark.anyio
async def test_unclassified_failure_hands_to_a_human_without_retrying(db_factory):
    """一个平台认不出来的失败,直接交给人,绝不自动重跑 (#574).

    Dev ran one topic the old way for 87 minutes: every crash scheduled the next
    turn, that turn crashed the same way, and only a deploy restart ever broke
    the chain — 228 events, and one user's 「1」 re-sent into a turn 80 times. An
    unnamed failure is a bug signal, not a transience signal — repeating it just
    triggers the same bug — so there is no chain: one event to a person, done."""
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)

    class _AlwaysBroken:
        """Fails with an exception no classifier recognises — where all 257 of
        dev's measured failures landed, every one with an empty meta.code."""

        session_factory = db_factory

        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.events: list[tuple[str, dict]] = []

        async def converse(self, **kw):
            self.calls.append(kw)
            raise RuntimeError("boom")
            yield  # pragma: no cover — makes this an async generator

        async def post_system_event(self, topic_id, content, turn_id=None, meta=None):
            self.events.append((content, meta or {}))
            return {"id": "sys", "kind": "event", "content": content, "meta": meta}

    svc = _AlwaysBroken()
    topic = await a_topic(db_factory)
    runner.submit(svc, topic, author="u", content="hi", summon=True)
    for _ in range(200):
        await asyncio.sleep(0.01)
        if any(meta.get("who") == "human" for _, meta in svc.events):
            break
    # Nothing must sneak a second turn in after the event lands.
    await asyncio.sleep(0.05)

    assert len(svc.calls) == 1, (
        f"未分类失败自动跑了 {len(svc.calls)} 轮 —— 不该自动重跑"
    )
    assert any(meta.get("who") == "human" for _, meta in svc.events), (
        "失败后没有把话题交给人:房间里没有一条 who=human 的事件"
    )


@pytest.mark.anyio
async def test_a_timeout_hands_to_a_human_without_retrying(db_factory):
    """超时那条路径和崩溃那条一样:不自动重跑,直接交给人 (#574).

    Both ends of `_execute` used to schedule the same auto-resume; now neither
    does. A turn that times out re-entering a wedged machine gains nothing from
    a re-run, so it fails loud once and waits for a person."""
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker, turn_timeout_s=0.01)

    class _AlwaysHangs:
        session_factory = db_factory

        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.events: list[tuple[str, dict]] = []

        async def converse(self, **kw):
            self.calls.append(kw)
            await asyncio.sleep(0.05)  # outlive the ceiling
            yield {"type": "done"}  # pragma: no cover

        async def post_system_event(self, topic_id, content, turn_id=None, meta=None):
            self.events.append((content, meta or {}))
            return {"id": "sys", "kind": "event", "content": content, "meta": meta}

    svc = _AlwaysHangs()
    topic = await a_topic(db_factory)
    runner.submit(svc, topic, author="u", content="hi", summon=True)
    for _ in range(400):
        await asyncio.sleep(0.005)
        if any(meta.get("who") == "human" for _, meta in svc.events):
            break
    # Nothing must sneak a second turn in after the timeout event lands.
    await asyncio.sleep(0.05)

    assert len(svc.calls) == 1, (
        f"超时自动跑了 {len(svc.calls)} 轮 —— 超时那条路径也不该自动重跑"
    )
    assert any(meta.get("who") == "human" for _, meta in svc.events), (
        "超时后没有把话题交给人"
    )


@pytest.mark.anyio
async def test_a_slow_setup_does_not_spend_the_ceiling_before_the_turn_starts(
    db_factory,
):
    """上限问的是「一轮活最多能活多久」，而准备数据库、挑后端、接屏幕都发生在这一轮
    真正开始之前。这些算进上限，一个还没接上屏幕的会话就会被判超时，而上限本身看起来
    是够用的：#617 里两个测试把上限压到 0.4 秒，CI 慢的时候准备阶段自己就超过 0.4 秒，
    于是判决在有东西可看之前就下了。

    冷启动保险丝故意仍然从最早算起，因为它问的是另一件事：这一轮到底有没有开始过。
    """
    broker = InProcessBroker()
    # Generic default long enough that the fuse is not what cuts here.
    runner = AgentWorkRunner(broker, turn_timeout_s=5.0)

    class _SlowSetup:
        session_factory = db_factory

        async def converse(self, **_):
            yield {"type": "turn_ceiling", "seconds": 0.3}
            # Setup: everything before the prompt reaches the session, and here
            # it takes longer than the whole declared ceiling.
            await asyncio.sleep(0.4)
            yield {"type": "prompt_delivered"}
            # The turn itself, comfortably inside its ceiling.
            await asyncio.sleep(0.1)
            yield {"type": "done"}

    topic = await a_topic(db_factory)
    async with broker.subscribe(str(topic)) as q:
        runner.submit(_SlowSetup(), topic, author="u", content="hi", summon=True)
        f = await _next_frame(q, "done", timeout=3)
    assert f["type"] == "done"


@pytest.mark.anyio
async def test_a_turn_cut_by_its_ceiling_still_ends_its_stream(db_factory):
    """超时那条路是把 chat 的生成器从中间切断的，所以 chat 自己那句 `done` 不会发；
    而 `turn_finished` 只对「已经宣布过自己开始」的一轮发，一个还在准备阶段就被切掉
    的轮次两个都没有。订阅者于是一直读到自己的读超时为止——0.4 秒的上限变成 300 秒的
    挂起就是这么来的，而失败本身是 1 秒内就知道的。
    """
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker, turn_timeout_s=0.05)

    class _NeverFinishes:
        session_factory = db_factory

        async def converse(self, **_):
            yield {"type": "prompt_delivered"}
            await asyncio.sleep(5)
            yield {"type": "done"}

    topic = await a_topic(db_factory)
    async with broker.subscribe(str(topic)) as q:
        runner.submit(_NeverFinishes(), topic, author="u", content="hi", summon=True)
        f = await _next_frame(q, "done", timeout=3)
    assert f["type"] == "done"
