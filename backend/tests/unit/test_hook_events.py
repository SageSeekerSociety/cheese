"""Hook → AgentEvent translation + the per-topic hook queue router."""

import pytest

from app.domain.agent.hook_events import (
    HookRouter,
    MessageAssembler,
    translate_hook,
)
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
async def test_router_delivers_to_subscribed_topic():
    router = HookRouter()
    sink = router.subscribe("t1")
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
    assert router.push("t1", {"a": 1}) is True
    assert (await sink.queue.get()) == {"a": 1}
    router.unsubscribe("t1", sink)
    assert router.push("t1", {"b": 2}) is False


@pytest.mark.anyio
async def test_router_delivers_between_platform_requests():
    router = HookRouter()
    sink = router.subscribe("t1")
    assert router.push("t1", {"a": 1}) is True
    assert (await sink.queue.get()) == {"a": 1}


# --- MessageAssembler: MessageDisplay flushes → whole messages ---------------
#
# Claude Code fires MessageDisplay once per batch of newly completed lines
# while an assistant message streams (payload verified against 2.1.224, the
# pinned device version, and 2.1.233 live): `message_id` is stable across the
# message's flushes, `index` increments per flush, exactly one flush carries
# `final: true`, and concatenating the deltas in index order reconstructs the
# message verbatim.


def _flush(
    mid: str, idx: int, delta: str, *, final: bool = False, eid: str | None = None
) -> dict:
    hook = {
        "hook_event_name": "MessageDisplay",
        "message_id": mid,
        "index": idx,
        "final": final,
        "delta": delta,
    }
    if eid is not None:
        hook["_eid"] = eid
    return hook


def test_multi_flush_message_coalesces_into_one_event():
    asm = MessageAssembler()
    assert asm.add(_flush("m1", 0, "line 1\nline 2\n", eid="e0")) is None
    assert asm.add(_flush("m1", 1, "line 3\n", eid="e1")) is None
    ev = asm.add(_flush("m1", 2, "line 4", final=True, eid="e2"))
    assert isinstance(ev, AgentMessage)
    assert ev.text == "line 1\nline 2\nline 3\nline 4"
    assert ev.eid == "e0"
    assert ev.eids == ("e0", "e1", "e2")


def test_single_flush_final_message_passes_through():
    ev = MessageAssembler().add(_flush("m1", 0, "hi", final=True, eid="e0"))
    assert isinstance(ev, AgentMessage)
    assert ev.text == "hi"
    assert ev.eids == ("e0",)


def test_final_flush_with_empty_delta_ends_the_message():
    # A message ending on a newline sends its last content in the prior flush;
    # the final flush is the end-of-message signal alone.
    asm = MessageAssembler()
    assert asm.add(_flush("m1", 0, "done\n", eid="e0")) is None
    ev = asm.add(_flush("m1", 1, "", final=True, eid="e1"))
    assert isinstance(ev, AgentMessage)
    assert ev.text == "done\n"
    assert ev.eids == ("e0", "e1")


def test_blank_message_is_suppressed():
    asm = MessageAssembler()
    assert asm.add(_flush("m1", 0, "  \n", eid="e0")) is None
    assert asm.add(_flush("m1", 1, "", final=True, eid="e1")) is None


def test_legacy_payload_without_flush_fields_is_one_message():
    # An older Claude Code (or a hand-built test payload) sends only `delta`:
    # keep the historical one-hook-one-message behavior.
    asm = MessageAssembler()
    ev = asm.add({"hook_event_name": "MessageDisplay", "delta": "hi", "_eid": "e9"})
    assert isinstance(ev, AgentMessage)
    assert ev.text == "hi"
    assert ev.eid == "e9"
    assert asm.add({"hook_event_name": "MessageDisplay", "delta": "   "}) is None


def test_redelivered_flush_is_dropped_by_index():
    # The device drainer is at-least-once: a flush whose ack was lost is
    # re-POSTed. The (message_id, index) pair identifies it exactly.
    asm = MessageAssembler()
    assert asm.add(_flush("m1", 0, "a\n", eid="e0")) is None
    assert asm.add(_flush("m1", 0, "a\n", eid="e0-again")) is None
    ev = asm.add(_flush("m1", 1, "b", final=True, eid="e1"))
    assert ev is not None
    assert ev.text == "a\nb"
    assert ev.eids == ("e0", "e1")


def test_redelivered_flush_of_a_completed_message_is_dropped():
    asm = MessageAssembler()
    ev = asm.add(_flush("m1", 0, "hi", final=True, eid="e0"))
    assert ev is not None
    assert asm.add(_flush("m1", 0, "hi", final=True, eid="e0")) is None


def test_out_of_order_flushes_wait_for_the_gap():
    # The drainer retries failed files while later ones may already have
    # landed, so index 2 can arrive before index 1. The message completes
    # only when every index up to the final one is present.
    asm = MessageAssembler()
    assert asm.add(_flush("m1", 0, "a\n", eid="e0")) is None
    assert asm.add(_flush("m1", 2, "c", final=True, eid="e2")) is None
    ev = asm.add(_flush("m1", 1, "b\n", eid="e1"))
    assert isinstance(ev, AgentMessage)
    assert ev.text == "a\nb\nc"
    assert ev.eids == ("e0", "e1", "e2")


def test_drain_flushes_partials_in_arrival_order():
    # Stop / turn end: whatever is still buffered must land rather than be
    # lost, joined from the flushes that did arrive.
    asm = MessageAssembler()
    assert asm.add(_flush("m1", 0, "first\n", eid="e0")) is None
    assert asm.add(_flush("m2", 0, "second", eid="e1")) is None
    drained = asm.drain()
    assert [ev.text for ev in drained] == ["first\n", "second"]
    assert [ev.eids for ev in drained] == [("e0",), ("e1",)]
    assert asm.drain() == []


def test_drain_suppresses_blank_partials():
    asm = MessageAssembler()
    assert asm.add(_flush("m1", 0, "   ", eid="e0")) is None
    assert asm.drain() == []
