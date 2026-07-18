"""Shared hooks substrate (fusion-design §8.6): the transport-independent core
both the local (tmux) and remote (device) backends run — settings wiring, the
cheese-hook forwarder, and the drain loop. Tested without Docker or a device."""

import asyncio
from pathlib import Path

import pytest

from app.domain.agent.hook_events import HookRouter
from app.domain.agent.hooks_substrate import (
    CHEESE_HOOK_SCRIPT,
    SESSION_TOKEN_TTL_S,
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
    names = ("SessionStart", "PreToolUse", "PostToolUse", "MessageDisplay", "Stop")
    for event in names:
        entry = s["hooks"][event][0]
        assert entry["hooks"][0] == {"type": "command", "command": "cheese-hook"}


def test_forwarder_spools_then_posts_hook_json_with_scoped_token():
    assert "X-Cheese-Token: $CHEESE_TOKEN" in CHEESE_HOOK_SCRIPT
    assert "X-Cheese-Event-Id: $eid" in CHEESE_HOOK_SCRIPT
    assert "--data-binary @-" in CHEESE_HOOK_SCRIPT
    # Durable-first: every hook is spooled (WAL) before the best-effort curl.
    assert "CHEESE_HOOK_SPOOL" in CHEESE_HOOK_SCRIPT
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
    events = await _drain(queue, turn_timeout_s=5, timeout_message="timeout")
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
    events = await _drain(queue, turn_timeout_s=0.05, timeout_message="轮次超时")
    assert len(events) == 1
    assert isinstance(events[0], AgentResult)
    assert events[0].is_error is True
    assert events[0].text == "轮次超时"


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

    provider = _NoRun(router=router, turn_timeout_s=1)
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
