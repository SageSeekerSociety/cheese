"""Shared hooks substrate (fusion-design §8.6): the transport-independent core
both the local (tmux) and remote (device) backends run — settings wiring, the
cheese-hook forwarder, and the drain loop. Tested without Docker or a device."""

import asyncio
import contextlib
import uuid as _uuid
from pathlib import Path

import pytest

from app.domain.agent.harness import Opening, SessionRef
from app.domain.agent.harness.claude_code.hook_events import HookRouter
from app.domain.agent.harness.claude_code.hooks_substrate import (
    CHEESE_HOOK_SCRIPT,
    SESSION_TOKEN_TTL_S,
    ActivityTracker,
    Channel,
    ClaudeCodeRuntime,
    ScreenSetupError,
    SessionActivity,
    WorkAttribution,
    monitor_session_activity,
)
from app.domain.agent.service import AgentMessage, AgentResult, AgentToolUse

pytestmark = pytest.mark.anyio


def test_forwarder_spools_then_posts_hook_json_with_scoped_token():
    assert "X-Cheese-Token: $CHEESE_TOKEN" in CHEESE_HOOK_SCRIPT
    assert "X-Cheese-Event-Id: $eid" in CHEESE_HOOK_SCRIPT
    assert "--data-binary @-" in CHEESE_HOOK_SCRIPT
    # Durable-first: every hook is spooled (WAL) before the best-effort curl.
    assert "CHEESE_HOOK_SPOOL" in CHEESE_HOOK_SCRIPT
    assert 'chmod 0777 "$CHEESE_HOOK_SPOOL"' in CHEESE_HOOK_SCRIPT
    # The inline curl is gated: unwired (no URL) or spool-only (device) → skip it, so
    # it never blocks a tool and the drainer / backend reconcile handle delivery.
    guard = '[ -z "$CHEESE_HOOK_SPOOL_ONLY" ] && [ -n "$CHEESE_HOOK_URL" ]'
    assert guard in CHEESE_HOOK_SCRIPT


def test_session_token_ttl_is_session_length():
    assert SESSION_TOKEN_TTL_S == 30 * 24 * 3600


def test_baked_forwarder_matches_the_single_source():
    """The tmux image COPYs backend/sandbox/cheese-hook; it MUST equal the
    substrate constant (the device launcher writes the same string at runtime).
    Regenerate with `scripts/gen-sandbox-assets.py` — this guards against drift."""
    baked = Path(__file__).resolve().parents[2] / "sandbox" / "cheese-hook"
    assert baked.is_file(), "run scripts/gen-sandbox-assets.py to emit it"
    assert baked.read_text(encoding="utf-8") == CHEESE_HOOK_SCRIPT


async def _drain(queue, **kw):
    events = []
    async for e in monitor_session_activity(queue=queue, resume_session_id=None, **kw):
        events.append(e)
    return events


async def test_monitor_session_activity_streams_in_order_and_ends_on_stop():
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "SessionStart", "session_id": "s1"})
    queue.put_nowait(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"cmd": "ls"},
        }
    )
    queue.put_nowait({"hook_event_name": "PostToolUse"})  # → None, skipped
    queue.put_nowait({"hook_event_name": "MessageDisplay", "delta": "hi"})
    queue.put_nowait(
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "done",
            "session_id": "s1",
        }
    )
    events = await _drain(
        queue, idle_suspect_s=5, hard_ceiling_s=5, timeout_message="timeout"
    )
    # PostToolUse produced no event; the rest stream in order, Stop ends it.
    assert isinstance(events[0].__class__, type)  # sanity
    types = [type(e).__name__ for e in events]
    assert types == ["AgentSessionInfo", "AgentToolUse", "AgentMessage", "AgentResult"]
    assert isinstance(events[1], AgentToolUse) and events[1].name == "Bash"
    assert isinstance(events[2], AgentMessage) and events[2].text == "hi"
    assert isinstance(events[-1], AgentResult) and events[-1].text == "done"
    assert events[-1].is_error is False


async def test_silence_ends_only_when_the_probe_says_the_process_is_gone():
    """一条 hook 都没有，本身不是结论。它让会话进入可疑，然后由探针去数进程；
    只有探针说进程没了才结束。硬上限到了也只记一笔，不再当判决。"""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})

    async def confirm_alive() -> bool:
        return False

    events = await _drain(
        queue,
        idle_suspect_s=0.05,
        hard_ceiling_s=5,
        timeout_message="轮次超时",
        confirm_alive=confirm_alive,
        confirm_poll_s=0.02,
    )
    assert len(events) == 1
    assert isinstance(events[0], AgentResult)
    assert events[0].is_error is True
    assert events[0].text == "轮次超时"


async def test_a_timed_out_turn_carries_its_own_classification():
    """超时这条失败是平台自己造的，所以它自己说自己是什么。

    过去它是靠在自己刚写下的那句话里找一个片段认出来的。改一个字——或者某个
    transport 换了自己的措辞——分类就丢了，房间里显示的是「AI 服务返回错误」，
    把排查的人指向一个根本没收到这轮请求的服务。这里故意用一句和原文毫无共同
    字词的文案，它照样得被认出来。

    现在产生这条失败的是「有输出、没进展」那道判据，所以会话得一直在说话。
    """
    from app.domain.agent.platform_failures import (
        TURN_TIMEOUT,
        classify_platform_failure,
    )

    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})

    async def keep_talking() -> None:
        while True:
            await asyncio.sleep(0.01)
            queue.put_nowait({"hook_event_name": "MessageDisplay", "delta": "还在说"})

    task = asyncio.create_task(keep_talking())
    try:
        events = await _drain(
            queue,
            idle_suspect_s=5,
            hard_ceiling_s=5,
            no_progress_s=0.1,
            timeout_message="完全不一样的一句话",
        )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    result = events[-1]
    assert isinstance(result, AgentResult) and result.is_error
    assert (
        classify_platform_failure(result.text, code=result.failure_code) is TURN_TIMEOUT
    )


async def test_an_undelivered_prompt_carries_its_own_classification():
    """同上：送不到芝士那边这条失败，分类也不再取决于那句话怎么写。"""
    from app.domain.agent.platform_failures import (
        PROMPT_UNDELIVERED,
        classify_platform_failure,
    )

    queue: asyncio.Queue[dict] = asyncio.Queue()
    events = await _drain(
        queue,
        idle_suspect_s=5,
        hard_ceiling_s=5,
        delivery_timeout_s=0.05,
        timeout_message="轮次超时",
        delivery_message="又是完全不一样的一句话",
    )
    result = events[-1]
    assert isinstance(result, AgentResult) and result.is_error
    assert (
        classify_platform_failure(result.text, code=result.failure_code)
        is PROMPT_UNDELIVERED
    )


async def test_stale_stop_before_screen_ready_never_ends_the_new_run():
    """A straggler before screen setup completes has no subscription yet and
    therefore cannot be mistaken for the new run's result."""
    import uuid as _uuid

    router = HookRouter()
    project_id = _uuid.uuid4()
    topic_id = _uuid.uuid4()
    topic_key = str(topic_id)
    stale_delivered: list[bool] = []

    class _FakeChannel(Channel):
        name = "fake"

        async def ensure_ready(self, **kwargs):
            # While "waiting for the screen", the abandoned previous turn's
            # `claude` process finally finishes and its late Stop arrives.
            stale_delivered.append(
                router.push(
                    topic_key,
                    {
                        "hook_event_name": "Stop",
                        "last_assistant_message": "旧turn的过期结果",
                        "session_id": "s-old",
                        "_eid": "stale-stop-1",
                    },
                )
            )
            return "screen"

        async def send_prompt(self, screen, prompt):
            # The new turn genuinely starts now — its own events follow.
            router.push(
                topic_key,
                {
                    "hook_event_name": "MessageDisplay",
                    "delta": "新turn的真实回复",
                    "_eid": "real-msg-1",
                },
            )
            router.push(
                topic_key,
                {
                    "hook_event_name": "Stop",
                    "last_assistant_message": "新turn的真实回复",
                    "session_id": "s-new",
                    "_eid": "real-stop-1",
                },
            )

    provider = ClaudeCodeRuntime(
        _FakeChannel(), router=router, idle_suspect_s=2, hard_ceiling_s=2
    )
    events = [
        e
        async for e in provider.run_turn(
            project_id=project_id,
            topic_id=topic_id,
            prompt="go",
            system_prompt="",
            resume_session_id="s-old",
        )
    ]
    types = [type(e).__name__ for e in events]
    assert types == ["AgentMessage", "AgentResult"]
    result = events[-1]
    assert isinstance(result, AgentResult)
    assert result.text == "新turn的真实回复"  # NOT the stale turn's text

    assert stale_delivered == [False]
    await provider._close_topic(topic_id)


async def test_failed_precheck_never_touches_the_router():
    """A turn that can't run at all (no Docker / no online device) must yield a
    clean error WITHOUT claiming the topic's queue — otherwise it would evict a
    live turn's queue (review finding; matches pre-refactor ordering)."""
    import uuid as _uuid

    class _NoRun(Channel):
        name = "no-run"

        async def precheck(self, project_id, topic_id):
            raise ScreenSetupError("挡在门外")

    router = HookRouter()
    topic_id = _uuid.uuid4()
    live_sink = router.subscribe(str(topic_id))

    provider = ClaudeCodeRuntime(
        _NoRun(), router=router, idle_suspect_s=1, hard_ceiling_s=1
    )
    events = [
        e
        async for e in provider.run_turn(
            project_id=_uuid.uuid4(),
            topic_id=topic_id,
            prompt="x",
            system_prompt="",
            resume_session_id=None,
        )
    ]
    assert len(events) == 1
    assert isinstance(events[0], AgentResult) and events[0].is_error
    assert events[0].text == "挡在门外"
    # The live turn's queue is untouched: pushes still reach it.
    assert router.push(str(topic_id), {"hook_event_name": "Stop"}) is True
    assert live_sink.queue.qsize() == 1
    router.unsubscribe(str(topic_id), live_sink)


async def test_undelivered_prompt_fails_fast_instead_of_waiting_out_the_turn():
    """A prompt typed into a terminal has no return value: tmux confirms the
    bytes reached the pane, nothing confirms a prompt box read them. When
    NOTHING comes back, the turn used to sit until the 900s ceiling (dev,
    2026-08-08). It must give up on the delivery window instead."""
    queue: asyncio.Queue[dict] = asyncio.Queue()

    events = await _drain(
        queue,
        idle_suspect_s=30,
        hard_ceiling_s=30,
        timeout_message="超时",
        delivery_timeout_s=0.3,
        delivery_message="没送到",
    )

    assert len(events) == 1
    assert events[0].is_error
    assert events[0].text == "没送到"


async def test_the_prompt_receipt_opens_the_full_turn_budget():
    """UserPromptSubmit is the receipt: once it lands, the turn is a normal one
    and only the turn ceiling applies."""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})
    queue.put_nowait(
        {"hook_event_name": "Stop", "last_assistant_message": "done", "session_id": "s"}
    )

    events = await _drain(
        queue,
        idle_suspect_s=30,
        hard_ceiling_s=30,
        timeout_message="超时",
        delivery_timeout_s=0.3,
        delivery_message="没送到",
    )

    # The receipt itself is bookkeeping, not something the topic should render.
    assert [type(e).__name__ for e in events] == ["AgentResult"]
    assert not events[0].is_error
    assert events[0].text == "done"


async def test_agent_activity_also_counts_as_delivery():
    """An older session may predate the receipt hook; any real activity proves
    the prompt landed just as well."""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "MessageDisplay", "delta": "working"})
    queue.put_nowait(
        {"hook_event_name": "Stop", "last_assistant_message": "ok", "session_id": "s"}
    )

    events = await _drain(
        queue,
        idle_suspect_s=30,
        hard_ceiling_s=30,
        timeout_message="超时",
        delivery_timeout_s=0.3,
        delivery_message="没送到",
    )

    assert [type(e).__name__ for e in events] == ["AgentMessage", "AgentResult"]


# --- turn 活跃度检测 (2026-08-09): the two-layer idle-suspect / hard-ceiling
# check that replaced the old single static deadline. Required verification
# (design brief): (1) a turn that's genuinely still alive must survive past the
# idle-suspect threshold, all the way to the hard ceiling; (2) a genuinely dead
# screen must still be caught — not left running until the hard ceiling.


async def test_idle_suspect_keeps_waiting_while_confirm_alive_says_alive():
    """No hooks arrive after delivery, but `confirm_alive` keeps saying the
    screen is alive (mirrors a long tool call with a busy tmux pane and no
    interim hook). The turn must NOT die at the idle-suspect threshold, must be
    re-probed more than once along the way (a single probe at minute 5 isn't
    enough — the screen could die at minute 6), and crossing the hard ceiling
    must be RECORDED rather than acted on: it ends when the session itself
    says Stop."""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})
    loop = asyncio.get_running_loop()
    tracker = ActivityTracker(last_at=loop.time())
    probe_calls = 0

    async def confirm_alive() -> bool:
        nonlocal probe_calls
        probe_calls += 1
        return True

    async def stop_later() -> None:
        await asyncio.sleep(0.4)
        queue.put_nowait(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "done",
                "session_id": "s1",
            }
        )

    stop_task = asyncio.create_task(stop_later())
    try:
        events = await _drain(
            queue,
            idle_suspect_s=0.05,
            hard_ceiling_s=0.25,
            timeout_message="硬顶到了",
            tracker=tracker,
            confirm_alive=confirm_alive,
            confirm_poll_s=0.05,
        )
    finally:
        stop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stop_task
    assert probe_calls >= 2
    assert tracker.ceiling_crossed_at is not None  # 到了，记下了
    assert events[-1].is_error is False  # 但没有因此结束
    assert events[-1].text == "done"


async def test_confirm_alive_false_ends_the_turn_well_before_the_hard_ceiling():
    """A genuinely dead screen must be caught by the idle-suspect probe and end
    the turn promptly — NOT be left running until a many-hours hard ceiling."""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})

    async def confirm_alive() -> bool:
        return False

    events = await _drain(
        queue,
        idle_suspect_s=0.05,
        hard_ceiling_s=100.0,  # would time this test out if idle-suspect didn't fire
        timeout_message="卡死了",
        confirm_alive=confirm_alive,
        confirm_poll_s=0.05,
    )
    assert len(events) == 1
    assert events[0].is_error
    assert events[0].text == "卡死了"


async def test_external_tracker_touch_clears_idle_suspicion():
    """A tracker fed by a transport side channel keeps the session out of
    idle-suspect entirely, so the probe is never asked. The hard ceiling is
    crossed along the way and recorded; what ends the session is its own
    Stop."""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})
    loop = asyncio.get_running_loop()
    tracker = ActivityTracker(last_at=loop.time())
    probe_calls = 0

    async def confirm_alive() -> bool:
        nonlocal probe_calls
        probe_calls += 1
        return True

    async def touch_periodically() -> None:
        for _ in range(12):
            await asyncio.sleep(0.03)
            tracker.touch(loop.time())
        queue.put_nowait(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "done",
                "session_id": "s1",
            }
        )

    touch_task = asyncio.create_task(touch_periodically())
    try:
        events = await _drain(
            queue,
            idle_suspect_s=0.05,
            hard_ceiling_s=0.25,
            timeout_message="硬顶到了",
            tracker=tracker,
            confirm_alive=confirm_alive,
            confirm_poll_s=0.02,
        )
    finally:
        touch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await touch_task

    assert probe_calls == 0
    assert tracker.ceiling_crossed_at is not None
    assert events[-1].is_error is False


async def test_deliver_reaches_the_screen_of_the_turn_in_flight():
    """A message posted while a turn is running must reach that turn's screen —
    the whole point of not queueing it behind the turn. `deliver` is only
    allowed to speak to a screen whose hook queue is registered, i.e. exactly
    while `run_turn` is between its screen handshake and its Stop."""
    import uuid as _uuid

    router = HookRouter()
    topic_id = _uuid.uuid4()
    topic_key = str(topic_id)
    injected: list[str] = []
    started = asyncio.Event()

    class _FakeChannel(Channel):
        name = "fake"

        async def ensure_ready(self, **kwargs):
            return "screen"

        async def send_prompt(self, screen, prompt):
            injected.append(prompt)
            if prompt == "第一条":
                started.set()
                return
            router.push(
                topic_key, {"hook_event_name": "UserPromptSubmit", "prompt": prompt}
            )

    provider = ClaudeCodeRuntime(
        _FakeChannel(), router=router, idle_suspect_s=2, hard_ceiling_s=2
    )

    # Before any turn: nothing to inject into, so the caller must run its own.
    assert await provider.deliver(topic_id, "早") is False

    async def run() -> list:
        return [
            event
            async for event in provider.run_turn(
                project_id=_uuid.uuid4(),
                topic_id=topic_id,
                prompt="第一条",
                system_prompt="",
                resume_session_id=None,
            )
        ]

    turn = asyncio.create_task(run())
    await asyncio.wait_for(started.wait(), 1)
    assert await provider.deliver(topic_id, "[人]: 等一下") is True
    router.push(
        topic_key,
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "好",
            "session_id": "s1",
            "_eid": "stop-1",
        },
    )
    events = await asyncio.wait_for(turn, 1)
    assert isinstance(events[-1], AgentResult)
    assert injected == ["第一条", "[人]: 等一下"]

    # The screen and subscription outlive the run, but its attribution is closed.
    assert await provider.deliver(topic_id, "晚") is False
    await provider._close_topic(topic_id)


async def test_deliver_reports_false_when_the_screen_refuses():
    """A screen that can't take the text must not be reported as delivered."""
    import uuid as _uuid

    router = HookRouter()
    topic_id = _uuid.uuid4()
    topic_key = str(topic_id)
    started = asyncio.Event()

    class _FakeChannel(Channel):
        name = "fake"

        async def ensure_ready(self, **kwargs):
            return "screen"

        async def send_prompt(self, screen, prompt):
            if prompt == "第一条":
                started.set()
                return
            raise ScreenSetupError("窗格已经死掉")

    provider = ClaudeCodeRuntime(
        _FakeChannel(), router=router, idle_suspect_s=2, hard_ceiling_s=2
    )

    async def run() -> list:
        return [
            event
            async for event in provider.run_turn(
                project_id=_uuid.uuid4(),
                topic_id=topic_id,
                prompt="第一条",
                system_prompt="",
                resume_session_id=None,
            )
        ]

    turn = asyncio.create_task(run())
    await asyncio.wait_for(started.wait(), 1)
    assert await provider.deliver(topic_id, "插一句") is False
    router.push(
        topic_key,
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "好",
            "session_id": "s1",
            "_eid": "stop-1",
        },
    )
    events = await asyncio.wait_for(turn, 1)
    assert isinstance(events[-1], AgentResult)
    await provider._close_topic(topic_id)


async def test_subscription_outlives_run_and_drops_only_with_screen():
    """Stop closes attribution, not the stable screen subscription."""
    import uuid as _uuid

    router = HookRouter()
    project_id = _uuid.uuid4()
    topic_id = _uuid.uuid4()
    topic_key = str(topic_id)

    class _FakeChannel(Channel):
        async def ensure_ready(self, **kwargs):
            return "screen"

        async def send_prompt(self, screen, prompt):
            router.push(
                topic_key,
                {
                    "hook_event_name": "Stop",
                    "last_assistant_message": "done",
                    "_eid": "stop-1",
                },
            )

    provider = ClaudeCodeRuntime(
        _FakeChannel(), router=router, idle_suspect_s=2, hard_ceiling_s=2
    )
    events = [
        event
        async for event in provider.run_turn(
            project_id=project_id,
            topic_id=topic_id,
            prompt="go",
            system_prompt="",
            resume_session_id=None,
        )
    ]

    assert isinstance(events[-1], AgentResult)
    subscription = await provider.ensure_subscription(project_id, topic_id)
    assert subscription.current_work is None
    assert subscription.consumer_task is not None
    assert not subscription.consumer_task.done()
    assert await provider.ensure_subscription(project_id, topic_id) is subscription

    await provider.drop_screen_subscription("screen")
    assert subscription.consumer_task.done()
    assert router.push(topic_key, {"hook_event_name": "Stop"}) is False


async def test_run_refuses_to_clobber_existing_attribution():
    """A second reader cannot replace the attribution feeding a live run."""
    import uuid as _uuid

    router = HookRouter()
    project_id = _uuid.uuid4()
    topic_id = _uuid.uuid4()

    class _FakeChannel(Channel):
        async def ensure_ready(self, **kwargs):
            return "screen"

        async def send_prompt(self, screen, prompt):
            return None

    provider = ClaudeCodeRuntime(
        _FakeChannel(), router=router, idle_suspect_s=2, hard_ceiling_s=2
    )
    subscription = await provider.ensure_subscription(project_id, topic_id)
    open_attribution = WorkAttribution(work_id=_uuid.uuid4(), queue=asyncio.Queue())
    subscription.current_work = open_attribution

    events = [
        event
        async for event in provider.run_turn(
            project_id=project_id,
            topic_id=topic_id,
            prompt="inspect",
            system_prompt="",
            resume_session_id=None,
        )
    ]

    assert len(events) == 1
    assert isinstance(events[0], AgentResult)
    assert events[0].is_error is True
    assert subscription.current_work is open_attribution
    await provider._close_topic(topic_id)


# --- MessageDisplay flush coalescing on the live subscription ---------------


def _display_flush(
    mid: str, idx: int, delta: str, *, final: bool = False, eid: str = ""
) -> dict:
    return {
        "hook_event_name": "MessageDisplay",
        "message_id": mid,
        "index": idx,
        "final": final,
        "delta": delta,
        "_eid": eid or f"{mid}-{idx}",
    }


async def test_run_turn_coalesces_message_flushes_into_one_message():
    """A streamed reply arrives as several MessageDisplay flushes; the turn
    must see ONE AgentMessage carrying the whole text (each flush used to
    become its own chat message — one reply, many bubbles)."""
    import uuid as _uuid

    router = HookRouter()
    project_id = _uuid.uuid4()
    topic_id = _uuid.uuid4()
    topic_key = str(topic_id)

    class _FakeChannel(Channel):
        async def ensure_ready(self, **kwargs):
            return "screen"

        async def send_prompt(self, screen, prompt):
            router.push(topic_key, _display_flush("m1", 0, "line 1\nline 2\n"))
            router.push(topic_key, _display_flush("m1", 1, "line 3", final=True))
            router.push(
                topic_key,
                {
                    "hook_event_name": "Stop",
                    "last_assistant_message": "line 1\nline 2\nline 3",
                    "session_id": "s1",
                    "_eid": "stop-1",
                },
            )

    provider = ClaudeCodeRuntime(
        _FakeChannel(), router=router, idle_suspect_s=2, hard_ceiling_s=2
    )
    events = [
        event
        async for event in provider.run_turn(
            project_id=project_id,
            topic_id=topic_id,
            prompt="go",
            system_prompt="",
            resume_session_id=None,
        )
    ]

    messages = [e for e in events if isinstance(e, AgentMessage)]
    assert [m.text for m in messages] == ["line 1\nline 2\nline 3"]
    assert messages[0].eids == ("m1-0", "m1-1")
    assert isinstance(events[-1], AgentResult)
    await provider._close_topic(topic_id)


async def test_run_turn_stop_drains_a_partial_message():
    """A message whose final flush never arrived still lands at Stop — the
    buffered lines must not die with the buffer."""
    import uuid as _uuid

    router = HookRouter()
    project_id = _uuid.uuid4()
    topic_id = _uuid.uuid4()
    topic_key = str(topic_id)

    class _FakeChannel(Channel):
        async def ensure_ready(self, **kwargs):
            return "screen"

        async def send_prompt(self, screen, prompt):
            router.push(topic_key, _display_flush("m1", 0, "第一行\n"))
            router.push(topic_key, _display_flush("m1", 1, "到这里就断了\n"))
            router.push(
                topic_key,
                {
                    "hook_event_name": "Stop",
                    "last_assistant_message": "",
                    "session_id": "s1",
                    "_eid": "stop-1",
                },
            )

    provider = ClaudeCodeRuntime(
        _FakeChannel(), router=router, idle_suspect_s=2, hard_ceiling_s=2
    )
    events = [
        event
        async for event in provider.run_turn(
            project_id=project_id,
            topic_id=topic_id,
            prompt="go",
            system_prompt="",
            resume_session_id=None,
        )
    ]

    types = [type(e).__name__ for e in events]
    assert types == ["AgentMessage", "AgentResult"]
    assert events[0].text == "第一行\n到这里就断了\n"
    await provider._close_topic(topic_id)


async def test_unsolicited_flushes_reach_the_consumer_as_one_message():
    """The screen-subscription consumer path (no turn listening) coalesces the
    same way, and the Stop's copy of the message is recognized as already
    seen — this exact miss is what stored a whole extra copy of every
    multi-flush reply."""
    import uuid as _uuid

    router = HookRouter()
    project_id = _uuid.uuid4()
    topic_id = _uuid.uuid4()
    topic_key = str(topic_id)

    class _FakeChannel(Channel):
        async def ensure_ready(self, **kwargs):
            return "screen"

        async def send_prompt(self, screen, prompt):
            return None

    provider = ClaudeCodeRuntime(
        _FakeChannel(), router=router, idle_suspect_s=2, hard_ceiling_s=2
    )
    consumed: list[tuple[object, str | None, bool]] = []

    async def consumer(
        project, topic, work_id, event, eid, result_text_seen, unsolicited
    ):
        consumed.append((event, eid, result_text_seen))

    provider.bind_events(consumer)
    await provider.ensure_subscription(project_id, topic_id)

    router.push(topic_key, _display_flush("m1", 0, "第一行\n"))
    router.push(topic_key, _display_flush("m1", 1, "第二行", final=True))
    router.push(
        topic_key,
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "第一行\n第二行",
            "session_id": "s1",
            "_eid": "stop-1",
        },
    )
    for _ in range(50):
        await asyncio.sleep(0.01)
        if any(isinstance(e, AgentResult) for e, _eid, _seen in consumed):
            break

    messages = [e for e, _eid, _seen in consumed if isinstance(e, AgentMessage)]
    assert [m.text for m in messages] == ["第一行\n第二行"]
    results = [(e, seen) for e, _eid, seen in consumed if isinstance(e, AgentResult)]
    assert len(results) == 1
    assert results[0][1] is True  # Stop's text matches the assembled message
    await provider._close_topic(topic_id)


# --- delivery verdicts must leave a server-side trace -------------------------
#
# 2026-08-17: a wave of false 「这条消息没能送到芝士那边」 banners was debugged
# with ZERO server-side evidence — every verdict below went straight into a room
# banner without a log line, so the only forensic record was a user's screenshot
# (issue #539). Each verdict now says what it decided and for which topic.


async def test_undelivered_verdict_logs_a_warning_with_context(caplog):
    queue: asyncio.Queue[dict] = asyncio.Queue()  # nothing ever arrives
    with caplog.at_level("WARNING"):
        events = await _drain(
            queue,
            idle_suspect_s=5,
            hard_ceiling_s=5,
            timeout_message="轮次超时",
            delivery_timeout_s=0.05,
            context="topic=t-undelivered",
        )
    assert isinstance(events[0], AgentResult) and events[0].is_error
    assert any(
        "t-undelivered" in r.getMessage() and "undelivered" in r.getMessage()
        for r in caplog.records
    )


async def test_hard_ceiling_crossing_logs_a_warning_with_context_and_does_not_end(
    caplog,
):
    """The ceiling is a fact, not a verdict. Crossing it must be visible in the
    log with the topic that crossed it, and must NOT end the turn: the session's
    own Stop does."""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "SessionStart", "session_id": "s1"})

    async def stop_later() -> None:
        await asyncio.sleep(0.3)
        queue.put_nowait(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "done",
                "session_id": "s1",
            }
        )

    task = asyncio.create_task(stop_later())
    try:
        with caplog.at_level("WARNING"):
            events = await _drain(
                queue,
                idle_suspect_s=0.05,
                hard_ceiling_s=0.15,
                timeout_message="轮次超时",
                context="topic=t-ceiling",
            )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert any(
        "t-ceiling" in r.getMessage() and "ceiling" in r.getMessage()
        for r in caplog.records
    )
    assert isinstance(events[-1], AgentResult) and events[-1].is_error is False


async def test_deliver_without_live_screen_logs_why(caplog):
    import uuid as _uuid

    class _FakeChannel(Channel):
        async def ensure_ready(self, **kwargs):
            return "screen"

        async def send_prompt(self, screen, prompt):
            return None

    provider = ClaudeCodeRuntime(_FakeChannel(), router=HookRouter())
    topic_id = _uuid.uuid4()
    with caplog.at_level("INFO"):
        assert await provider.deliver(topic_id, "hi") is False
    assert any(
        str(topic_id) in r.getMessage() and "no live screen" in r.getMessage()
        for r in caplog.records
    )


# --- receipt semantics: write-accept IS delivery (#539 decision A) -----------
#
# The transport already promises "a write either reaches the process or
# returns an error" (#487). UserPromptSubmit fires when the session CONSUMES
# the message — often minutes later on a busy session — so gating deliver()
# on a 25s receipt wait manufactured false 「没能送到」 banners for messages
# that were sitting safely in claude's own input queue. The receipt's real
# jobs are the consumed stamp and the record, both via the receipt consumer.


async def test_deliver_trusts_write_accept_without_waiting_for_a_receipt(
    monkeypatch,
):
    import time as _time
    import uuid as _uuid

    from app.domain.agent.harness.claude_code import hooks_substrate as hs

    monkeypatch.setattr(hs, "DELIVERY_TIMEOUT_S", 0.3)
    router = HookRouter()
    topic_id = _uuid.uuid4()
    started = asyncio.Event()

    class _FakeChannel(Channel):
        name = "fake"

        async def ensure_ready(self, **kwargs):
            return "screen"

        async def send_prompt(self, screen, prompt):
            # Write accepted; the session is busy — NO UserPromptSubmit comes
            # back for a long while. That must not read as "undelivered".
            if prompt == "第一条":
                started.set()

    provider = ClaudeCodeRuntime(
        _FakeChannel(), router=router, idle_suspect_s=2, hard_ceiling_s=2
    )

    async def run() -> list:
        return [
            event
            async for event in provider.run_turn(
                project_id=_uuid.uuid4(),
                topic_id=topic_id,
                prompt="第一条",
                system_prompt="",
                resume_session_id=None,
            )
        ]

    turn = asyncio.create_task(run())
    await asyncio.wait_for(started.wait(), 1)
    t0 = _time.monotonic()
    assert await provider.deliver(topic_id, "[人]: 等一下") is True
    assert _time.monotonic() - t0 < 0.25  # returned on write-accept, no wait
    router.push(
        str(topic_id),
        {"hook_event_name": "Stop", "last_assistant_message": "好", "_eid": "s1"},
    )
    await asyncio.wait_for(turn, 1)
    await provider._close_topic(topic_id)


async def test_user_prompt_submit_is_reported_to_the_receipt_consumer():
    import uuid as _uuid

    router = HookRouter()
    project_id = _uuid.uuid4()
    topic_id = _uuid.uuid4()
    received: list[tuple[object, str]] = []

    class _FakeChannel(Channel):
        async def ensure_ready(self, **kwargs):
            return "screen"

        async def send_prompt(self, screen, prompt):
            return None

    provider = ClaudeCodeRuntime(
        _FakeChannel(), router=router, idle_suspect_s=2, hard_ceiling_s=2
    )

    async def on_receipt(tid, prompt):
        received.append((tid, prompt))

    provider.bind_receipts(on_receipt)
    await provider.ensure_subscription(project_id, topic_id)
    router.push(
        str(topic_id),
        {"hook_event_name": "UserPromptSubmit", "prompt": "[人]: 等一下"},
    )
    for _ in range(50):
        await asyncio.sleep(0.01)
        if received:
            break
    assert received == [(topic_id, "[人]: 等一下")]
    await provider._close_topic(topic_id)


class _AliveScreen(Channel):
    """A session that accepts everything and stays up — the ordinary case."""

    async def ensure_ready(self, **kwargs):
        return "screen"

    async def send_prompt(self, screen, prompt):
        return True


async def test_cancelling_a_consumer_during_activity_cleanup_stops_it():
    """Cancellation during a child's cleanup must not restart the hook loop."""
    router = HookRouter()
    provider = ClaudeCodeRuntime(_AliveScreen(), router=router)
    reported = []

    async def on_activity(_project, _topic, _work, active):
        reported.append(active)

    provider.bind_activity(on_activity)
    project_id, topic_id = _uuid.uuid4(), _uuid.uuid4()
    subscription = await provider.ensure_subscription(project_id, topic_id)
    child_started = asyncio.Event()
    child_stopping = asyncio.Event()
    release_child = asyncio.Event()

    async def slow_activity_cleanup():
        child_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            child_stopping.set()
            await release_child.wait()

    child = asyncio.create_task(slow_activity_cleanup())
    subscription.activity = SessionActivity(
        work_id=_uuid.uuid4(), queue=asyncio.Queue(), ready=True, task=child
    )
    consumer = subscription.consumer_task
    assert consumer is not None
    try:
        await asyncio.wait_for(child_started.wait(), timeout=1)
        router.push(
            str(topic_id),
            {"hook_event_name": "Stop", "last_assistant_message": "done"},
        )
        await asyncio.wait_for(child_stopping.wait(), timeout=1)
        consumer.cancel()
        done, _ = await asyncio.wait({consumer}, timeout=1)
        assert consumer in done, "cancelled consumer went back to waiting for hooks"
        assert consumer.cancelled()
        assert reported == [False]
    finally:
        release_child.set()
        for task in (consumer, child):
            task.cancel()
        await asyncio.gather(consumer, child, return_exceptions=True)
        await provider._close_topic(topic_id)


async def test_every_turn_reported_started_is_also_reported_finished():
    """一轮报了开始，就必须报结束——哪怕它是烂尾的。

    ``bind_activity`` is how the platform knows a topic is busy: it lights the
    room's 正在思考, it is what a redeploy drains on, and it is what stops a
    second turn from starting on top of a live one. So an unmatched "started"
    costs more than a frame — the topic carries that mark for the life of the
    process, and every prompt after it is refused as 「已有工作正在运行」.

    The turn here dies the way a turn dies when nothing on the machine answers:
    no hook ever arrives, the watchdog calls it undelivered, and the consumer
    that would record that verdict raises on its way to the database. That is
    one lost turn. It must not also be a lost topic.
    """
    import uuid as _uuid

    router = HookRouter()
    project_id, topic_id = _uuid.uuid4(), _uuid.uuid4()
    reported: list[tuple[_uuid.UUID, bool]] = []

    async def watch_activity(_project, _topic, work_id, active):
        reported.append((work_id, active))

    async def consume_and_fail(*_args, **_kwargs):
        raise RuntimeError("数据库连接没了")

    provider = ClaudeCodeRuntime(
        _AliveScreen(),
        router=router,
        idle_suspect_s=1,
        hard_ceiling_s=1,
        # The verdict this test is about. The ceiling used to arrive first and
        # stand in for it; the ceiling no longer ends anything.
        delivery_timeout_s=0.2,
    )
    provider.bind_activity(watch_activity)
    provider.bind_events(consume_and_fail)

    await provider.send(
        SessionRef(project_id=project_id, topic_id=topic_id),
        "go",
        Opening(system_prompt=""),
        work_id=_uuid.uuid4(),
        on_mark=lambda _work_id: None,
    )

    # The watch runs alongside the turn, so give it its own ending rather than
    # assuming the turn's last frame was also its last act.
    for _ in range(300):
        if any(not active for _work, active in reported):
            break
        await asyncio.sleep(0.01)

    started = {work for work, active in reported if active}
    finished = {work for work, active in reported if not active}
    assert started, "这一轮根本没报告过开始，测试没测到东西"
    assert started == finished, f"报了开始没报结束：{started - finished}，话题从此卡住"
    await provider._close_topic(topic_id)


# --- 什么才算「会话正在干活」 -------------------------------------------------
#
# 房间里那句「芝士正在处理…」由一段 activity 撑着，而在正常路径上，只有会话自己
# 的 Stop 会撤掉它。开的条件和关的条件必须对得上：任何一个钩子都能开、只有 Stop
# 能关，就是一笔永远平不了的账。


async def _one_turn(provider, router, topic_key, project_id, topic_id, consumed):
    """Send a prompt and let the session answer it, exactly once."""
    import uuid as _uuid

    await provider.send(
        SessionRef(project_id=project_id, topic_id=topic_id),
        "go",
        Opening(system_prompt=""),
        work_id=_uuid.uuid4(),
        on_mark=lambda _work_id: None,
    )
    router.push(
        topic_key,
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "done",
            "session_id": "s1",
            "_eid": "stop-1",
        },
    )
    for _ in range(300):
        await asyncio.sleep(0.01)
        if any(isinstance(e, AgentResult) for e in consumed):
            return
    raise AssertionError("这一轮没有结束，后面测的东西都不成立")


def _provider_with_ledger():
    """A runtime plus the two ledgers these cases read: activity and events."""
    router = HookRouter()
    reported: list[tuple[object, bool]] = []
    consumed: list[object] = []

    async def watch_activity(_project, _topic, work_id, active):
        reported.append((work_id, active))

    async def consumer(_p, _t, _work_id, event, _eid, _seen, _unsolicited):
        consumed.append(event)

    provider = ClaudeCodeRuntime(
        _AliveScreen(), router=router, idle_suspect_s=30, hard_ceiling_s=30
    )
    provider.bind_activity(watch_activity)
    provider.bind_events(consumer)
    return provider, router, reported, consumed


@pytest.mark.parametrize(
    ("label", "hook"),
    [
        # 每次 resume、每次自动 compact 都会再发一遍，是线上最常撞到的那个。
        ("SessionStart", {"hook_event_name": "SessionStart", "session_id": "s1"}),
        ("UserPromptSubmit", {"hook_event_name": "UserPromptSubmit", "prompt": "hi"}),
        (
            "PostToolUse",
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Read",
                "tool_response": "x",
            },
        ),
        # 分身的起止说的是「会话里多了/少了一个工人」，不是「会话正在答」。
        # 而且分身跨得过轮次边界：它可以在会话早就停下之后才结束，那时候不会再
        # 有任何 Stop 来关掉这个标记。
        (
            "SubagentStart",
            {
                "hook_event_name": "SubagentStart",
                "agent_id": "w1",
                "agent_type": "general-purpose",
            },
        ),
        (
            "SubagentStop",
            {
                "hook_event_name": "SubagentStop",
                "agent_id": "w1",
                "last_assistant_message": "查完了",
            },
        ),
    ],
)
async def test_a_hook_that_is_not_the_session_working_opens_nothing(label, hook):
    """轮次结束之后飘来的钩子，不能点亮一个没人会去关的「正在处理」。

    这些钩子说的都不是「会话正在答」：会话起来了、有人敲了字、一个工具在答案给完
    之后才回来。它们后面不会跟一个 Stop，所以一旦拿它们开了 activity，那个标记就
    一直立到三小时的硬上限——而且每有一个客户端连上来，`turn_active` 就把它重新
    塞给对方一次。刷新页面清不掉它，因为要清的东西根本不在页面这边。
    """
    import uuid as _uuid

    provider, router, reported, consumed = _provider_with_ledger()
    project_id, topic_id = _uuid.uuid4(), _uuid.uuid4()
    topic_key = str(topic_id)

    await _one_turn(provider, router, topic_key, project_id, topic_id, consumed)
    assert {w for w, a in reported if a} == {w for w, a in reported if not a}

    router.push(topic_key, {**hook, "_eid": f"stray-{label}"})
    for _ in range(60):
        await asyncio.sleep(0.01)

    started = {w for w, a in reported if a}
    finished = {w for w, a in reported if not a}
    assert started == finished, f"{label} 之后房间卡在正在处理：{started - finished}"
    await provider._close_topic(topic_id)


async def test_the_session_working_on_its_own_still_lights_the_room():
    """有人直接在机器上的会话里干活，房间照样要看得见——这是上面那条规则不能顺手
    砍掉的东西。第一个「它在产出」的钩子点亮房间，会话的 Stop 熄灭它。"""
    import uuid as _uuid

    provider, router, reported, consumed = _provider_with_ledger()
    project_id, topic_id = _uuid.uuid4(), _uuid.uuid4()
    topic_key = str(topic_id)

    await _one_turn(provider, router, topic_key, project_id, topic_id, consumed)
    before = len({w for w, a in reported if a})

    router.push(
        topic_key, {"hook_event_name": "SessionStart", "session_id": "s1", "_eid": "e1"}
    )
    router.push(
        topic_key,
        {"hook_event_name": "UserPromptSubmit", "prompt": "改一下", "_eid": "e2"},
    )
    router.push(
        topic_key,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "_eid": "e3",
        },
    )
    for _ in range(200):
        await asyncio.sleep(0.01)
        if len({w for w, a in reported if a}) > before:
            break
    assert len({w for w, a in reported if a}) > before, "会话自己在干活，房间没亮"

    router.push(
        topic_key,
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "改完了",
            "session_id": "s1",
            "_eid": "e4",
        },
    )
    for _ in range(300):
        await asyncio.sleep(0.01)
        if {w for w, a in reported if a} == {w for w, a in reported if not a}:
            break
    started = {w for w, a in reported if a}
    finished = {w for w, a in reported if not a}
    assert started == finished, f"会话干完了，房间还亮着：{started - finished}"
    await provider._close_topic(topic_id)


async def test_nobody_accuses_a_self_running_session_of_never_hearing_us():
    """投递看门狗看的是「投喂进去的话，会话接到了吗」。会话自己开始干活的那一轮压根
    没有投喂 —— 要是它照样被算进去，房间里会冒出一行「消息没送进芝士的会话」，说的
    是一条从来不存在的消息。

    它不会，而且不是靠豁免：开出这段 activity 的就是会话产出的那个钩子，那个钩子
    同一批进了 activity 的队列，投递因此当场成立。
    """
    import uuid as _uuid

    from app.domain.agent.platform_failures import PROMPT_UNDELIVERED_CODE

    router = HookRouter()
    consumed: list[object] = []
    reported: list[tuple[object, bool]] = []

    async def consumer(_p, _t, _work_id, event, _eid, _seen, _unsolicited):
        consumed.append(event)

    async def watch_activity(_project, _topic, work_id, active):
        reported.append((work_id, active))

    # 投递窗口掐到 50ms：真要误判，这个测试会当场看见。
    provider = ClaudeCodeRuntime(
        _AliveScreen(),
        router=router,
        idle_suspect_s=30,
        hard_ceiling_s=30,
        delivery_timeout_s=0.05,
    )
    provider.bind_events(consumer)
    provider.bind_activity(watch_activity)
    project_id, topic_id = _uuid.uuid4(), _uuid.uuid4()
    topic_key = str(topic_id)
    await provider.ensure_subscription(project_id, topic_id)
    provider._live[topic_id] = "screen"

    router.push(
        topic_key,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "_eid": "own-1",
        },
    )
    for _ in range(60):
        await asyncio.sleep(0.01)

    # 先确认看门狗真的在跑 —— 不然「没有误判」只是因为压根没人判过。
    assert [w for w, a in reported if a], "会话自己在产出，房间没亮，这条测试等于没测"
    failures = [
        e
        for e in consumed
        if isinstance(e, AgentResult)
        and e.is_error
        and e.failure_code == PROMPT_UNDELIVERED_CODE
    ]
    assert not failures, f"会话自己在干活，平台却说消息没送到：{failures}"
    await provider._close_topic(topic_id)


# --- 图片输入: an image that cannot be staged costs the image, not the message ---
#
# The bytes live in the backend's worktree. A screen on another machine can only
# open them once they have been copied across, and that copy can fail for
# reasons that have nothing to do with the message: a connector too old to know
# the file frame (measured 2026-08-23 — the deployed binary predated `file.put`
# by a day, so every frame was dropped unanswered and the send timed out), a
# wedged machine, bytes that are no longer there. When staging lived inside the
# send, that failure took the whole message with it: the room showed nothing at
# all, while plain-text messages around it arrived normally.


class _StagingChannel(Channel):
    """A screen whose machine refuses the images it is offered."""

    name = "staging"

    def __init__(self, *, refuse: bool) -> None:
        self._refuse = refuse
        self.prompts: list[str] = []

    async def ensure_ready(self, **kwargs):
        return "screen"

    async def stage_images(self, screen, images):
        if self._refuse:
            return [], list(images)
        return list(images), []

    async def send_prompt(self, screen, prompt):
        self.prompts.append(prompt)
        return True


async def _deliver_one_image(channel: _StagingChannel) -> str | None:
    router = HookRouter()
    topic_id = _uuid.uuid4()
    project_id = _uuid.uuid4()
    router.subscribe(str(topic_id))
    runtime = ClaudeCodeRuntime(channel, router=router)
    subscription = await runtime.ensure_subscription(project_id, topic_id)
    subscription.current_work = WorkAttribution(
        work_id=_uuid.uuid4(), queue=asyncio.Queue()
    )
    runtime._live[topic_id] = "screen"
    delivered = await runtime.deliver(
        topic_id,
        "[fulu] 看看这张截图",
        images=[{"path": "uploads/img-1.png", "media_type": "image/png"}],
    )
    return channel.prompts[0] if delivered else None


async def test_an_image_that_cannot_be_staged_still_delivers_the_words():
    channel = _StagingChannel(refuse=True)
    prompt = await _deliver_one_image(channel)

    assert prompt is not None, "the message must arrive even when the image does not"
    assert "[fulu] 看看这张截图" in prompt


async def test_an_unstaged_image_is_declared_rather_than_mentioned():
    """Naming a path that is not on the machine produces silence — Claude Code
    resolves the mention to nothing — and 芝士 answers about a picture it was
    never shown. Say what happened instead."""
    channel = _StagingChannel(refuse=True)
    prompt = await _deliver_one_image(channel)

    assert prompt is not None
    assert "@uploads/img-1.png" not in prompt
    assert "没能送到" in prompt
    assert "不要猜图里是什么" in prompt


async def test_a_staged_image_is_mentioned_and_nothing_is_declared_missing():
    channel = _StagingChannel(refuse=False)
    prompt = await _deliver_one_image(channel)

    assert prompt is not None
    assert "@uploads/img-1.png" in prompt
    assert "没能送到" not in prompt


async def test_a_channel_that_raises_while_staging_does_not_lose_the_message():
    """A channel is expected to report losses rather than raise, but it talks to
    a machine over a network. The runtime picks the message over the picture."""

    class _Exploding(_StagingChannel):
        async def stage_images(self, screen, images):
            raise RuntimeError("connector went away mid-write")

    channel = _Exploding(refuse=False)
    prompt = await _deliver_one_image(channel)

    assert prompt is not None
    assert "[fulu] 看看这张截图" in prompt
    assert "没能送到" in prompt


# --- 第三道判据：会话还在产出，但已经不再读进任何东西 ------------------------
#
# 前两道判据看的都是会话「产出」什么：多久没有 hook、跑了多久。一个停止读取输入
# 的会话照样产出，所以那两道永远不会为它响。它做不到的是接住下一句话，而那是
# 唯一一种「有人在等」的失败。


async def test_an_injected_message_left_unread_ends_the_session():
    """注入的消息超过宽限期还没被消费，这个会话就该结束。

    结束不是丢弃：那条消息仍然留在待消费列表里，下一轮会重放它。
    """
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})
    loop = asyncio.get_running_loop()
    written_at = loop.time() - 10  # 十秒前写进去的，至今没有回执

    events = await _drain(
        queue,
        idle_suspect_s=30,
        hard_ceiling_s=30,
        timeout_message="不该是这句",
        delivery_message="读不进去",
        unread_since=lambda: written_at,
        unread_grace_s=0.05,
    )
    assert len(events) == 1
    assert events[0].is_error
    # 是「读不进去」而不是「超时」：两道判据的结论不能混，房间里显示的原因不同。
    assert events[0].text == "读不进去"


async def test_nothing_waiting_means_this_check_never_fires():
    """没有人在等的时候，这道判据完全不参与，会话照旧由探针管：这里探针说
    进程没了，结束的是那条路，措辞是超时那句而不是「读不进去」。"""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})

    async def confirm_alive() -> bool:
        return False

    events = await _drain(
        queue,
        idle_suspect_s=0.05,
        hard_ceiling_s=5,
        timeout_message="轮次超时",
        delivery_message="读不进去",
        unread_since=lambda: None,
        unread_grace_s=0.05,
        confirm_alive=confirm_alive,
        confirm_poll_s=0.02,
    )
    assert len(events) == 1
    assert events[0].text == "轮次超时"


async def test_a_message_still_inside_its_grace_does_not_end_anything():
    """刚注入的消息不算读不进去。一个跑长命令的会话在工具返回之前本来就读不到
    输入，宽限期就是留给这种情况的。会话由自己的 Stop 正常结束。"""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})
    loop = asyncio.get_running_loop()
    just_now = loop.time()

    async def stop_later() -> None:
        await asyncio.sleep(0.2)
        queue.put_nowait(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "done",
                "session_id": "s1",
            }
        )

    task = asyncio.create_task(stop_later())
    try:
        events = await _drain(
            queue,
            idle_suspect_s=5,
            hard_ceiling_s=5,
            timeout_message="轮次超时",
            delivery_message="读不进去",
            unread_since=lambda: just_now,
            unread_grace_s=30,
        )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert events[-1].is_error is False
    assert events[-1].text == "done"


# --- 第二道判据：在说话，但没在干活 --------------------------------------------
#
# 陷在循环里的会话每隔几秒发一条 MessageDisplay，在「有没有动静」眼里它一直活着，
# 探针数进程也一直在。它做不到的是调工具或者收尾。这道判据看的就是「最后一次
# 输出比最后一次进展新，而且离最后一次进展已经太久」。


async def test_output_with_no_progress_ends_the_session():
    """一直在吐字、一次工具都不调：这就是这道判据要拦的那个会话。"""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})

    async def keep_talking() -> None:
        while True:
            await asyncio.sleep(0.01)
            queue.put_nowait({"hook_event_name": "MessageDisplay", "delta": "还在说"})

    task = asyncio.create_task(keep_talking())
    try:
        events = await _drain(
            queue,
            idle_suspect_s=5,
            hard_ceiling_s=5,
            no_progress_s=0.1,
            timeout_message="光说不做",
        )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert events[-1].is_error is True
    assert events[-1].text == "光说不做"


async def test_a_session_that_spoke_once_and_went_quiet_belongs_to_the_probe():
    """说过一句然后沉默：这不是「在说话没干活」，是安静。安静归探针管，探针说
    活着就一直等；这道判据不许因为很久以前的一句话就把它判掉。"""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})
    queue.put_nowait({"hook_event_name": "MessageDisplay", "delta": "说一句"})

    async def confirm_alive() -> bool:
        return True

    async def stop_later() -> None:
        await asyncio.sleep(0.4)
        queue.put_nowait(_stop())

    task = asyncio.create_task(stop_later())
    try:
        events = await _drain(
            queue,
            idle_suspect_s=0.05,
            hard_ceiling_s=30,
            no_progress_s=0.1,
            timeout_message="光说不做",
            confirm_alive=confirm_alive,
            confirm_poll_s=0.02,
        )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert events[-1].is_error is False
    assert events[-1].text == "done"


async def test_output_interleaved_with_tool_calls_is_work():
    """每次输出之间都有一次工具调用，进展的时钟一直在刷新，这道判据不会响。"""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})

    async def keep_working() -> None:
        for _ in range(6):
            await asyncio.sleep(0.03)
            queue.put_nowait({"hook_event_name": "MessageDisplay", "delta": "看一下"})
            await asyncio.sleep(0.03)
            queue.put_nowait(
                {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {}}
            )
        queue.put_nowait(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "done",
                "session_id": "s1",
            }
        )

    task = asyncio.create_task(keep_working())
    try:
        events = await _drain(
            queue,
            idle_suspect_s=5,
            hard_ceiling_s=5,
            no_progress_s=0.1,
            timeout_message="光说不做",
        )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert events[-1].is_error is False


async def test_a_long_tool_call_produces_no_output_and_is_left_alone():
    """长命令的形状：一条 PreToolUse 之后什么都没有。没有输出，所以「输出比进展新」
    不成立，这道判据不碰它；它由探针管，探针说活着就一直等。"""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})
    queue.put_nowait(
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}}
    )

    async def confirm_alive() -> bool:
        return True

    async def stop_later() -> None:
        await asyncio.sleep(0.25)
        queue.put_nowait(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "done",
                "session_id": "s1",
            }
        )

    task = asyncio.create_task(stop_later())
    try:
        events = await _drain(
            queue,
            idle_suspect_s=0.05,
            hard_ceiling_s=5,
            no_progress_s=0.05,
            timeout_message="光说不做",
            confirm_alive=confirm_alive,
            confirm_poll_s=0.02,
        )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert events[-1].is_error is False


# --- 第二道关卡不看工具在飞的时候 ---------------------------------------------
#
# 输入是在工具边界被读走的。一条消息在一个 40 分钟的命令跑着的时候注入，
# 它读不到不是聋了，是还没到能读的那一刻。
#
# 注入都在会话已经进入工具之后才发生（`injected["at"]` 由一个任务稍后填），
# 因为那才是这条判据真正面对的顺序：先有工具在飞，然后有人说话。


def _stop() -> dict:
    return {
        "hook_event_name": "Stop",
        "last_assistant_message": "done",
        "session_id": "s1",
    }


async def test_an_unread_message_during_a_tool_call_is_not_a_verdict():
    """PreToolUse 之后没有 PostToolUse，工具在飞：注入多久没被读都不算。"""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})
    queue.put_nowait(
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}}
    )
    loop = asyncio.get_running_loop()
    injected: dict[str, float | None] = {"at": None}

    async def confirm_alive() -> bool:
        return True

    async def inject_then_stop() -> None:
        await asyncio.sleep(0.05)
        injected["at"] = loop.time() - 100  # already far past any grace
        await asyncio.sleep(0.2)
        queue.put_nowait(_stop())

    task = asyncio.create_task(inject_then_stop())
    try:
        events = await _drain(
            queue,
            idle_suspect_s=0.05,
            hard_ceiling_s=30,
            timeout_message="轮次超时",
            delivery_message="读不进去",
            unread_since=lambda: injected["at"],
            unread_grace_s=0.05,
            confirm_alive=confirm_alive,
            confirm_poll_s=0.02,
        )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert events[-1].is_error is False
    assert events[-1].text == "done"


async def test_the_unread_clock_starts_over_when_the_tool_returns():
    """工具返回之后，等待从返回那一刻起算，而不是从注入起算：会话在这个边界
    上才第一次有机会读到它，宽限期要给在这之后。"""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})
    queue.put_nowait(
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}}
    )
    loop = asyncio.get_running_loop()
    injected: dict[str, float | None] = {"at": None}

    async def inject_return_stop() -> None:
        await asyncio.sleep(0.05)
        injected["at"] = loop.time() - 100
        await asyncio.sleep(0.05)
        queue.put_nowait(
            {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_response": {}}
        )
        # Inside the grace measured from the return; the injection itself is
        # ancient. If the clock ran from injection this would have fired.
        await asyncio.sleep(0.1)
        queue.put_nowait(_stop())

    task = asyncio.create_task(inject_return_stop())
    try:
        events = await _drain(
            queue,
            idle_suspect_s=30,
            hard_ceiling_s=30,
            timeout_message="轮次超时",
            delivery_message="读不进去",
            unread_since=lambda: injected["at"],
            unread_grace_s=1.0,
        )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert events[-1].is_error is False
    assert events[-1].text == "done"


async def test_an_unread_message_after_the_tool_returned_still_counts():
    """工具返回、宽限期从返回算起过完、还是没读：这才是聋了。"""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})
    queue.put_nowait(
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}}
    )
    loop = asyncio.get_running_loop()
    injected: dict[str, float | None] = {"at": None}

    async def inject_then_return() -> None:
        await asyncio.sleep(0.05)
        injected["at"] = loop.time() - 100
        await asyncio.sleep(0.05)
        queue.put_nowait(
            {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_response": {}}
        )

    task = asyncio.create_task(inject_then_return())
    try:
        events = await _drain(
            queue,
            idle_suspect_s=30,
            hard_ceiling_s=30,
            timeout_message="轮次超时",
            delivery_message="读不进去",
            unread_since=lambda: injected["at"],
            unread_grace_s=0.1,
        )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert events[-1].is_error is True
    assert events[-1].text == "读不进去"
