"""Hook → AgentEvent translation + the per-topic hook queue router."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.agent.harness.claude_code.hook_events import (
    RECORDED_AT_KEY,
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


def test_post_tool_use_of_a_plain_tool_has_no_event():
    """Only the subagent tools surface their return value (test_subagent_result_
    events.py) — everything else's is already visible through its effect."""
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
# while an assistant message streams (payload verified live against 2.1.224,
# 2.1.233 and 2.1.261, the pinned device version): `message_id` is stable across the
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


# --- 说出口的时刻，不是拼装完成的时刻 -----------------------------------------
# A message is only known to be COMPLETE once something after it arrives, so the
# moment assembly finishes is systematically later than the moment 芝士 said the
# words — by however long the tool call that follows took to reach us. The room
# sorts on the block's timestamp, so using the later one files a message behind
# the tool call it actually introduced (observed live: 「先看代码链路。」 rendered
# between the two greps it announced).


def _stamped(mid: str, idx: int, delta: str, *, final: bool, at=None) -> dict:
    hook = _flush(mid, idx, delta, final=final)
    if at is not None:
        hook[RECORDED_AT_KEY] = at
    return hook


def test_an_assembled_message_is_stamped_when_it_started_not_when_it_finished():
    started = datetime(2026, 8, 23, 15, 57, 47, tzinfo=UTC)
    finished = datetime(2026, 8, 23, 15, 58, 30, tzinfo=UTC)
    assembler = MessageAssembler()

    assert assembler.add(_stamped("m1", 0, "先看", final=False, at=started)) is None
    message = assembler.add(_stamped("m1", 1, "代码链路。", final=True, at=finished))

    assert isinstance(message, AgentMessage)
    assert message.text == "先看代码链路。"
    assert message.at == started


def test_a_flush_that_arrives_late_does_not_move_the_message_later():
    """Flushes can arrive out of order — a retried spool file lands after the
    ones behind it. The stamp is the earliest, not the first one handled."""
    early = datetime(2026, 8, 23, 15, 57, 47, tzinfo=UTC)
    late = datetime(2026, 8, 23, 15, 57, 49, tzinfo=UTC)
    assembler = MessageAssembler()

    assert assembler.add(_stamped("m1", 1, "世界", final=True, at=late)) is None
    message = assembler.add(_stamped("m1", 0, "你好", final=False, at=early))

    assert isinstance(message, AgentMessage)
    assert message.at == early


def test_a_message_drained_by_a_stop_keeps_its_own_start_time():
    started = datetime(2026, 8, 23, 15, 57, 47, tzinfo=UTC)
    assembler = MessageAssembler()
    assembler.add(_stamped("m1", 0, "半句话", final=False, at=started))

    events = assembler.translate(
        {"hook_event_name": "Stop", RECORDED_AT_KEY: started + timedelta(minutes=5)}
    )

    drained = [e for e in events if isinstance(e, AgentMessage)]
    assert [m.at for m in drained] == [started]


def test_an_unstamped_hook_falls_back_to_now():
    """The live path handles a hook as it arrives, so it stamps nothing and
    'now' is the honest answer. Only a backfill pass has to say otherwise."""
    before = datetime.now(UTC)
    message = MessageAssembler().add(_stamped("m1", 0, "你好", final=True))
    assert isinstance(message, AgentMessage)
    assert message.at is not None
    assert before <= message.at <= datetime.now(UTC)


# --- StopFailure：API 拒绝了这一轮 ------------------------------------------------
#
# Claude Code 在这种情况下发的是 StopFailure 而不是 Stop（2.1.224 和 2.1.260 上
# 实测，529/402/429/401 都如此）。不接它，这一轮在平台这边就永远不结束。


def test_stop_failure_ends_the_turn_as_an_error():
    from app.domain.agent.harness.claude_code.hook_events import translate_hook
    from app.domain.agent.service import AgentResult

    result = translate_hook(
        {
            "hook_event_name": "StopFailure",
            "error": "server_error",
            "last_assistant_message": "API Error: Repeated 529 Overloaded errors.",
            "session_id": "s1",
        }
    )
    assert isinstance(result, AgentResult)
    assert result.is_error is True
    assert result.session_id == "s1"
    assert "Repeated 529" in result.text
    assert result.errors == ["server_error"]
    # 不带 failure_code：`error` 字段不可靠（代理返回的 429 被读成
    # authentication_failed），房间里那句话交给文本路径去定。
    assert result.failure_code is None


def test_stop_failure_carries_the_error_kind_into_the_text():
    """余额那类错误要能被 chat 层的 out-of-credit 标记认出来，`billing` 得在文本里。"""
    from app.domain.agent.harness.claude_code.hook_events import translate_hook

    result = translate_hook(
        {
            "hook_event_name": "StopFailure",
            "error": "billing_error",
            "last_assistant_message": "Your credit balance is too low.",
            "session_id": "s1",
        }
    )
    assert result is not None and result.is_error
    assert "billing_error" in result.text


def test_stop_failure_with_no_message_still_says_something():
    from app.domain.agent.harness.claude_code.hook_events import translate_hook

    result = translate_hook({"hook_event_name": "StopFailure", "error": "rate_limit"})
    assert result is not None and result.is_error
    assert "rate_limit" in result.text


def test_the_launch_subscribes_to_stop_failure():
    """不订阅它，API 错误结束的一轮就没有任何结束信号。"""
    from app.domain.agent.harness.claude_code.session_launch import hooks_settings

    assert "StopFailure" in hooks_settings()["hooks"]
