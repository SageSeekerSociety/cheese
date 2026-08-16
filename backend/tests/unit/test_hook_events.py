"""Hook → AgentEvent translation + the per-topic hook queue router."""

import pytest

from app.domain.agent.hook_events import HookRouter, translate_hook
from app.domain.agent.service import (
    AgentMessage,
    AgentResult,
    AgentSessionInfo,
    AgentToolUse,
)


def test_session_start_maps_to_session_info():
    ev = translate_hook({"hook_event_name": "SessionStart", "session_id": "abc"})
    assert isinstance(ev, AgentSessionInfo)
    assert ev.session_id == "abc"


def test_session_start_without_id_is_dropped():
    assert translate_hook({"hook_event_name": "SessionStart"}) is None


def test_pre_tool_use_maps_to_tool_use():
    ev = translate_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
    )
    assert isinstance(ev, AgentToolUse)
    assert ev.name == "Bash"
    assert ev.input == {"command": "ls"}


def test_pre_tool_use_non_dict_input_becomes_empty():
    ev = translate_hook(
        {"hook_event_name": "PreToolUse", "tool_name": "X", "tool_input": "oops"}
    )
    assert isinstance(ev, AgentToolUse)
    assert ev.input == {}


def test_message_display_maps_to_message():
    ev = translate_hook({"hook_event_name": "MessageDisplay", "delta": "你好"})
    assert isinstance(ev, AgentMessage)
    assert ev.text == "你好"


def test_message_display_blank_is_dropped():
    assert translate_hook({"hook_event_name": "MessageDisplay", "delta": "   "}) is None
    assert translate_hook({"hook_event_name": "MessageDisplay"}) is None


def test_post_tool_use_has_no_event():
    assert (
        translate_hook(
            {"hook_event_name": "PostToolUse", "tool_name": "Bash", "duration_ms": 5}
        )
        is None
    )


def test_stop_maps_to_result_with_text_and_session():
    ev = translate_hook(
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "done",
            "session_id": "s9",
        }
    )
    assert isinstance(ev, AgentResult)
    assert ev.text == "done"
    assert ev.session_id == "s9"
    assert ev.is_error is False
    assert ev.usage is not None and ev.usage.total_tokens == 0


def test_stop_reads_usage_when_present():
    ev = translate_hook(
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "ok",
            "usage": {"input_tokens": 10, "output_tokens": 3, "cost_usd": 0.01},
        }
    )
    assert isinstance(ev, AgentResult)
    assert ev.usage is not None
    assert ev.usage.input_tokens == 10
    assert ev.usage.output_tokens == 3


def test_delivery_failed_becomes_a_typed_event_for_the_provider_loop():
    """#445: the driver's give-up must reach the ACTIVE turn as a typed event
    (the provider re-sends on it), never die in the connector's journal."""
    from app.domain.agent.service import AgentDeliveryFailure

    ev = translate_hook(
        {"hook_event_name": "CheeseDeliveryFailed", "phase": "paste", "ticks": 41}
    )
    assert isinstance(ev, AgentDeliveryFailure)
    assert ev.phase == "paste" and ev.ticks == 41


def test_delivery_retried_surfaces_as_a_visible_message():
    """A flaky-but-successful delivery is worth a room line before it becomes
    a dead turn."""
    ev = translate_hook(
        {"hook_event_name": "CheeseDeliveryRetried", "phase": "submit", "ticks": 12}
    )
    assert isinstance(ev, AgentMessage)
    assert "12" in ev.text


def test_unknown_event_is_dropped():
    assert translate_hook({"hook_event_name": "SubagentStop"}) is None
    assert translate_hook({}) is None


def test_camelcase_event_name_alias():
    ev = translate_hook({"hookEventName": "SessionStart", "session_id": "z"})
    assert isinstance(ev, AgentSessionInfo)


@pytest.mark.anyio
async def test_router_delivers_to_registered_topic():
    router = HookRouter()
    sink = router.subscribe("t1")
    sink.accepting = True
    assert router.push("t1", {"hook_event_name": "Stop"}) is True
    assert (await sink.queue.get())["hook_event_name"] == "Stop"


def test_router_push_without_listener_returns_false():
    router = HookRouter()
    assert router.push("nobody", {"x": 1}) is False


@pytest.mark.anyio
async def test_router_subscription_is_stable_until_its_owner_unsubscribes():
    router = HookRouter()
    sink = router.subscribe("t1")
    assert router.subscribe("t1") is sink
    sink.accepting = True
    assert router.push("t1", {"a": 1}) is True
    assert (await sink.queue.get()) == {"a": 1}
    router.unsubscribe("t1", sink)
    assert router.push("t1", {"b": 2}) is False


@pytest.mark.anyio
async def test_router_queues_between_turns_but_reports_undelivered_in_p1():
    router = HookRouter()
    sink = router.subscribe("t1")
    assert router.push("t1", {"a": 1}) is False
    assert (await sink.queue.get()) == {"a": 1}
