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
    HooksTurnProvider,
    ScreenSetupError,
    hooks_settings,
    run_hooks_turn,
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
    async for e in run_hooks_turn(queue=queue, resume_session_id=None, **kw):
        events.append(e)
    return events


async def test_run_hooks_turn_streams_in_order_and_ends_on_stop():
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


async def test_run_hooks_turn_times_out_with_message_on_silence():
    queue: asyncio.Queue[dict] = asyncio.Queue()  # nothing ever arrives
    events = await _drain(
        queue, idle_suspect_s=0.05, hard_ceiling_s=0.05, timeout_message="轮次超时"
    )
    assert len(events) == 1
    assert isinstance(events[0], AgentResult)
    assert events[0].is_error is True
    assert events[0].text == "轮次超时"


async def test_stale_stop_from_abandoned_turn_never_ends_the_new_turn(
    monkeypatch, tmp_path
):
    """register() claims the topic's queue BEFORE the screen is ready (so no
    hook is missed) — but that means a straggler from a PREVIOUS, abandoned
    turn (its own late Stop included, arriving only once its `claude` process
    finally finishes) can land in the fresh queue before the new turn's prompt
    is even sent. It must never be mistaken for the new turn's own result."""
    import uuid as _uuid

    from app.core.config import settings
    from app.domain.agent import event_spool
    from app.domain.workspace import service as ws

    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    router = HookRouter()
    project_id = _uuid.uuid4()
    topic_id = _uuid.uuid4()
    topic_key = str(topic_id)

    class _FakeProvider(HooksTurnProvider[str]):
        name = "fake"

        async def _ensure_ready(self, **kwargs):
            # While "waiting for the screen", the abandoned previous turn's
            # `claude` process finally finishes and its late Stop arrives.
            router.push(
                topic_key,
                {
                    "hook_event_name": "Stop",
                    "last_assistant_message": "旧turn的过期结果",
                    "session_id": "s-old",
                    "_eid": "stale-stop-1",
                },
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

    # The stale Stop was never dropped — it's parked for a later reconcile.
    entries = event_spool.spool_entries(ws.spool_dir(project_id, topic_id))
    eids = [eid for _path, eid, _payload in entries]
    assert eids == ["stale-stop-1"]


async def test_failed_precheck_never_touches_the_router():
    """A turn that can't run at all (no Docker / no online device) must yield a
    clean error WITHOUT claiming the topic's queue — otherwise it would evict a
    live turn's queue (review finding; matches pre-refactor ordering)."""
    import uuid as _uuid

    class _NoRun(HooksTurnProvider[str]):
        name = "no-run"

        async def _precheck(self, project_id, topic_id):
            raise ScreenSetupError("挡在门外")

    router = HookRouter()
    topic_id = _uuid.uuid4()
    live_queue = router.register(str(topic_id))  # a "running turn" holds the slot

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
    assert live_queue.qsize() == 1


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

    class _FakeProvider(HooksTurnProvider[str]):
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

    # The turn is over: the screen is unpublished again, so a later message
    # cannot be injected into a window where no hook queue is listening.
    assert await provider.deliver(topic_id, "晚") is False


async def test_deliver_reports_false_when_the_screen_refuses():
    """A screen that can't take the text must not be reported as delivered."""
    import uuid as _uuid

    router = HookRouter()
    topic_id = _uuid.uuid4()
    topic_key = str(topic_id)
    started = asyncio.Event()

    class _FakeProvider(HooksTurnProvider[str]):
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


async def test_deliver_requires_the_matching_prompt_receipt(monkeypatch):
    """Activity from the live turn cannot acknowledge a different message."""
    import uuid as _uuid

    from app.domain.agent import hooks_substrate as substrate

    monkeypatch.setattr(substrate, "DELIVERY_TIMEOUT_S", 0.05)
    router = HookRouter()
    topic_id = _uuid.uuid4()
    topic_key = str(topic_id)
    started = asyncio.Event()

    class _FakeProvider(HooksTurnProvider[str]):
        name = "fake"

        async def _ensure_ready(self, **kwargs):
            return "screen"

        async def _send_prompt(self, screen, prompt):
            if prompt == "第一条":
                started.set()
                return
            router.push(
                topic_key,
                {"hook_event_name": "UserPromptSubmit", "prompt": "别的消息"},
            )

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
    assert await provider.deliver(topic_id, "目标消息") is False
    router.push(
        topic_key,
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "好",
            "session_id": "s1",
            "_eid": "stop-1",
        },
    )
    await asyncio.wait_for(turn, 1)
