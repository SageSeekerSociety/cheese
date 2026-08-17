"""Shared hooks substrate (fusion-design §8.6): the transport-independent core
both the local (tmux) and remote (device) backends run — settings wiring, the
cheese-hook forwarder, and the drain loop. Tested without Docker or a device."""

import asyncio
import contextlib
from pathlib import Path

import pytest

from app.domain.agent.hook_events import HookRouter
from app.domain.agent.hooks_substrate import (
    CHEESE_HOOK_SCRIPT,
    SESSION_TOKEN_TTL_S,
    ActivityTracker,
    HooksSessionProvider,
    ScreenSetupError,
    WorkAttribution,
    hooks_settings,
    monitor_session_activity,
)
from app.domain.agent.service import AgentMessage, AgentResult, AgentToolUse

pytestmark = pytest.mark.anyio


def test_hooks_settings_wire_every_perception_hook_to_the_forwarder():
    s = hooks_settings()
    assert s["skipDangerousModePermissionPrompt"] is True
    names = (
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "MessageDisplay",
        "Stop",
    )
    for event in names:
        entry = s["hooks"][event][0]
        assert entry["hooks"][0] == {"type": "command", "command": "cheese-hook"}


def test_hooks_settings_deny_the_tool_no_user_can_answer():
    """AskUserQuestion's picker is drawn inside the screen's terminal, out of
    every user's reach — a turn that calls it waits forever. Both hooks backends
    read this settings.json, so the deny belongs here, next to the hook wiring."""
    assert hooks_settings()["permissions"]["deny"] == ["AskUserQuestion"]


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


async def test_monitor_session_activity_times_out_with_message_on_silence():
    queue: asyncio.Queue[dict] = asyncio.Queue()  # nothing ever arrives
    events = await _drain(
        queue, idle_suspect_s=0.05, hard_ceiling_s=0.05, timeout_message="轮次超时"
    )
    assert len(events) == 1
    assert isinstance(events[0], AgentResult)
    assert events[0].is_error is True
    assert events[0].text == "轮次超时"


async def test_stale_stop_before_screen_ready_never_ends_the_new_run():
    """A straggler before screen setup completes has no subscription yet and
    therefore cannot be mistaken for the new run's result."""
    import uuid as _uuid

    router = HookRouter()
    project_id = _uuid.uuid4()
    topic_id = _uuid.uuid4()
    topic_key = str(topic_id)
    stale_delivered: list[bool] = []

    class _FakeProvider(HooksSessionProvider[str]):
        name = "fake"

        async def _ensure_ready(self, **kwargs):
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

        async def _send_prompt(self, screen, prompt):
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

    provider = _FakeProvider(router=router, idle_suspect_s=2, hard_ceiling_s=2)
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
    await provider.drop_subscription(topic_id)


async def test_failed_precheck_never_touches_the_router():
    """A turn that can't run at all (no Docker / no online device) must yield a
    clean error WITHOUT claiming the topic's queue — otherwise it would evict a
    live turn's queue (review finding; matches pre-refactor ordering)."""
    import uuid as _uuid

    class _NoRun(HooksSessionProvider[str]):
        name = "no-run"

        async def _precheck(self, project_id, topic_id):
            raise ScreenSetupError("挡在门外")

    router = HookRouter()
    topic_id = _uuid.uuid4()
    live_sink = router.subscribe(str(topic_id))

    provider = _NoRun(router=router, idle_suspect_s=1, hard_ceiling_s=1)
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
    interim hook) — the turn must NOT die at the idle-suspect threshold, only
    at the hard ceiling, and it must have been re-probed more than once along
    the way (a single probe at minute 5 isn't enough — the screen could die at
    minute 6)."""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})
    probe_calls = 0

    async def confirm_alive() -> bool:
        nonlocal probe_calls
        probe_calls += 1
        return True

    events = await _drain(
        queue,
        idle_suspect_s=0.05,
        hard_ceiling_s=0.25,
        timeout_message="硬顶到了",
        confirm_alive=confirm_alive,
        confirm_poll_s=0.05,
    )
    assert len(events) == 1
    assert events[0].is_error
    assert events[0].text == "硬顶到了"  # the HARD ceiling ended it, not idle-suspect
    assert probe_calls >= 2


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
    """A backend-specific activity signal ALONGSIDE hooks (the tmux backend's
    capture-pane polling) must count as activity just like a hook arrival does
    — an externally-touched tracker keeps the turn out of idle-suspect
    entirely, so `confirm_alive` is never even called."""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})
    loop = asyncio.get_event_loop()
    tracker = ActivityTracker(last_at=loop.time())
    probe_calls = 0

    async def confirm_alive() -> bool:
        nonlocal probe_calls
        probe_calls += 1
        return True

    async def touch_periodically() -> None:
        for _ in range(8):
            await asyncio.sleep(0.03)
            tracker.touch(loop.time())

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

    assert events[0].text == "硬顶到了"
    assert probe_calls == 0


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

    class _FakeProvider(HooksSessionProvider[str]):
        name = "fake"

        async def _ensure_ready(self, **kwargs):
            return "screen"

        async def _send_prompt(self, screen, prompt):
            injected.append(prompt)
            if prompt == "第一条":
                started.set()
                return
            router.push(
                topic_key, {"hook_event_name": "UserPromptSubmit", "prompt": prompt}
            )

    provider = _FakeProvider(router=router, idle_suspect_s=2, hard_ceiling_s=2)

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
    await provider.drop_subscription(topic_id)


async def test_deliver_reports_false_when_the_screen_refuses():
    """A screen that can't take the text must not be reported as delivered."""
    import uuid as _uuid

    router = HookRouter()
    topic_id = _uuid.uuid4()
    topic_key = str(topic_id)
    started = asyncio.Event()

    class _FakeProvider(HooksSessionProvider[str]):
        name = "fake"

        async def _ensure_ready(self, **kwargs):
            return "screen"

        async def _send_prompt(self, screen, prompt):
            if prompt == "第一条":
                started.set()
                return
            raise ScreenSetupError("窗格已经死掉")

    provider = _FakeProvider(router=router, idle_suspect_s=2, hard_ceiling_s=2)

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
    await provider.drop_subscription(topic_id)


async def test_subscription_outlives_run_and_drops_only_with_screen():
    """Stop closes attribution, not the stable screen subscription."""
    import uuid as _uuid

    router = HookRouter()
    project_id = _uuid.uuid4()
    topic_id = _uuid.uuid4()
    topic_key = str(topic_id)

    class _FakeProvider(HooksSessionProvider[str]):
        async def _ensure_ready(self, **kwargs):
            return "screen"

        async def _send_prompt(self, screen, prompt):
            router.push(
                topic_key,
                {
                    "hook_event_name": "Stop",
                    "last_assistant_message": "done",
                    "_eid": "stop-1",
                },
            )

    provider = _FakeProvider(router=router, idle_suspect_s=2, hard_ceiling_s=2)
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

    class _FakeProvider(HooksSessionProvider[str]):
        async def _ensure_ready(self, **kwargs):
            return "screen"

        async def _send_prompt(self, screen, prompt):
            return None

    provider = _FakeProvider(router=router, idle_suspect_s=2, hard_ceiling_s=2)
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
    await provider.drop_subscription(topic_id)


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

    class _FakeProvider(HooksSessionProvider[str]):
        async def _ensure_ready(self, **kwargs):
            return "screen"

        async def _send_prompt(self, screen, prompt):
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

    provider = _FakeProvider(router=router, idle_suspect_s=2, hard_ceiling_s=2)
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
    await provider.drop_subscription(topic_id)


async def test_run_turn_stop_drains_a_partial_message():
    """A message whose final flush never arrived still lands at Stop — the
    buffered lines must not die with the buffer."""
    import uuid as _uuid

    router = HookRouter()
    project_id = _uuid.uuid4()
    topic_id = _uuid.uuid4()
    topic_key = str(topic_id)

    class _FakeProvider(HooksSessionProvider[str]):
        async def _ensure_ready(self, **kwargs):
            return "screen"

        async def _send_prompt(self, screen, prompt):
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

    provider = _FakeProvider(router=router, idle_suspect_s=2, hard_ceiling_s=2)
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
    await provider.drop_subscription(topic_id)


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

    class _FakeProvider(HooksSessionProvider[str]):
        async def _ensure_ready(self, **kwargs):
            return "screen"

        async def _send_prompt(self, screen, prompt):
            return None

    provider = _FakeProvider(router=router, idle_suspect_s=2, hard_ceiling_s=2)
    consumed: list[tuple[object, str | None, bool]] = []

    async def consumer(
        project, topic, work_id, event, eid, result_text_seen, unsolicited
    ):
        consumed.append((event, eid, result_text_seen))

    provider.bind_event_consumer(consumer)
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
    await provider.drop_subscription(topic_id)


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


async def test_hard_ceiling_verdict_logs_a_warning_with_context(caplog):
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "SessionStart", "session_id": "s1"})
    with caplog.at_level("WARNING"):
        events = await _drain(
            queue,
            idle_suspect_s=0.05,
            hard_ceiling_s=0.15,
            timeout_message="轮次超时",
            context="topic=t-ceiling",
        )
    assert isinstance(events[-1], AgentResult) and events[-1].is_error
    assert any(
        "t-ceiling" in r.getMessage() and "ceiling" in r.getMessage()
        for r in caplog.records
    )


async def test_deliver_without_live_screen_logs_why(caplog):
    import uuid as _uuid

    class _FakeProvider(HooksSessionProvider[str]):
        async def _ensure_ready(self, **kwargs):
            return "screen"

        async def _send_prompt(self, screen, prompt):
            return None

    provider = _FakeProvider(router=HookRouter())
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

    from app.domain.agent import hooks_substrate as hs

    monkeypatch.setattr(hs, "DELIVERY_TIMEOUT_S", 0.3)
    router = HookRouter()
    topic_id = _uuid.uuid4()
    started = asyncio.Event()

    class _FakeProvider(HooksSessionProvider[str]):
        name = "fake"

        async def _ensure_ready(self, **kwargs):
            return "screen"

        async def _send_prompt(self, screen, prompt):
            # Write accepted; the session is busy — NO UserPromptSubmit comes
            # back for a long while. That must not read as "undelivered".
            if prompt == "第一条":
                started.set()

    provider = _FakeProvider(router=router, idle_suspect_s=2, hard_ceiling_s=2)

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
    await provider.drop_subscription(topic_id)


async def test_user_prompt_submit_is_reported_to_the_receipt_consumer():
    import uuid as _uuid

    router = HookRouter()
    project_id = _uuid.uuid4()
    topic_id = _uuid.uuid4()
    received: list[tuple[object, str]] = []

    class _FakeProvider(HooksSessionProvider[str]):
        async def _ensure_ready(self, **kwargs):
            return "screen"

        async def _send_prompt(self, screen, prompt):
            return None

    provider = _FakeProvider(router=router, idle_suspect_s=2, hard_ceiling_s=2)

    async def on_receipt(tid, prompt):
        received.append((tid, prompt))

    provider.bind_receipt_consumer(on_receipt)
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
    await provider.drop_subscription(topic_id)


async def test_prompt_redelivery_logs_each_attempt(caplog):
    import uuid as _uuid

    router = HookRouter()
    project_id = _uuid.uuid4()
    topic_id = _uuid.uuid4()
    topic_key = str(topic_id)
    sends: list[str] = []

    class _FakeProvider(HooksSessionProvider[str]):
        async def _ensure_ready(self, **kwargs):
            return "screen"

        async def _send_prompt(self, screen, prompt):
            sends.append(prompt)
            if len(sends) == 1:
                router.push(
                    topic_key,
                    {
                        "hook_event_name": "CheeseDeliveryFailed",
                        "phase": "paste",
                        "ticks": 3,
                    },
                )
            else:
                router.push(
                    topic_key,
                    {"hook_event_name": "Stop", "last_assistant_message": "好"},
                )

    provider = _FakeProvider(router=router, idle_suspect_s=2, hard_ceiling_s=2)
    with caplog.at_level("WARNING"):
        events = [
            e
            async for e in provider.run_turn(
                project_id=project_id,
                topic_id=topic_id,
                prompt="go",
                system_prompt="",
                resume_session_id=None,
            )
        ]
    assert len(sends) == 2  # original + one redelivery
    assert isinstance(events[-1], AgentResult)
    assert any(
        str(topic_id) in r.getMessage() and "redeliver" in r.getMessage()
        for r in caplog.records
    )
    await provider.drop_subscription(topic_id)
