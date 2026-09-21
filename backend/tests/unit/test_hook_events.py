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
    STEP_ERROR_MAX,
    AgentMessage,
    AgentResult,
    AgentSessionInfo,
    AgentStepFailed,
    AgentSubagentStart,
    AgentSubagentStop,
    AgentToolResult,
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
    assert translate_hook({"hook_event_name": "PreCompact"}) is None
    assert translate_hook({}) is None


# --- subagents: one session, several workers -------------------------------
#
# Payload shapes below are the ones a real claude sends (2.1.224), field for
# field. A hook we mis-read is not a crash — it is a subagent's work quietly
# filed under the wrong worker, or under nobody.

_SUBAGENT_START = {
    "hook_event_name": "SubagentStart",
    "agent_id": "a8a5aea3b68767861",
    "agent_type": "general-purpose",
    "cwd": "/work",
    "prompt_id": "p1",
    "session_id": "s1",
    "transcript_path": "/home/u/.claude/projects/w/s1.jsonl",
}

_SUBAGENT_STOP = {
    "hook_event_name": "SubagentStop",
    "agent_id": "a8a5aea3b68767861",
    "agent_transcript_path": "/home/u/.claude/projects/w/sub.jsonl",
    "agent_type": "general-purpose",
    "background_tasks": [],
    "cwd": "/work",
    "effort": "high",
    "last_assistant_message": "查完了：三条结论都成立。",
    "permission_mode": "bypassPermissions",
    "prompt_id": "p1",
    "session_id": "s1",
    "stop_hook_active": False,
    "transcript_path": "/home/u/.claude/projects/w/s1.jsonl",
}


def test_subagent_start_names_the_worker():
    ev = translate_hook(_SUBAGENT_START)
    assert isinstance(ev, AgentSubagentStart)
    assert ev.agent_id == "a8a5aea3b68767861"
    assert ev.agent_type == "general-purpose"
    assert ev.session_id == "s1"


def test_subagent_stop_carries_the_answer_home():
    """一个分身的收尾话只到派它的那个线程，跟着容器的 transcript 一起没。
    平台唯一能拿到它的时刻就是这条钩子。"""
    ev = translate_hook(_SUBAGENT_STOP)
    assert isinstance(ev, AgentSubagentStop)
    assert ev.agent_id == "a8a5aea3b68767861"
    assert ev.text == "查完了：三条结论都成立。"
    assert ev.agent_type == "general-purpose"
    assert ev.transcript_path == "/home/u/.claude/projects/w/sub.jsonl"
    assert ev.session_id == "s1"


def test_subagent_stop_with_nothing_said_is_still_an_event():
    """分身可以一句话不说就交回来——事件照发，因为「它停了」本身就是要知道的。"""
    ev = translate_hook({**_SUBAGENT_STOP, "last_assistant_message": ""})
    assert isinstance(ev, AgentSubagentStop)
    assert ev.text == ""


@pytest.mark.parametrize("event_name", ["SubagentStart", "SubagentStop"])
@pytest.mark.parametrize("bad_id", [None, "", "   "])
def test_a_subagent_with_no_id_is_dropped(event_name, bad_id):
    """没有 id 的分身事件不能放行：整套机制就是靠 id 把后面的钩子归到某个工人
    头上，而一个没名字的工人和会话本身分不开——放行等于把活记在一个再也对不上
    的名字底下。"""
    hook = {"hook_event_name": event_name}
    if bad_id is not None:
        hook["agent_id"] = bad_id
    assert translate_hook(hook) is None


def test_the_main_thread_is_the_absence_of_an_id():
    """主线程的钩子根本没有 agent_id 这个 key（不是 null，是没有），所以
    「没有 id」就是「会话自己」——不需要再去别处对账。"""
    ev = translate_hook(
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}}
    )
    assert isinstance(ev, AgentToolUse)
    assert ev.agent_id is None
    assert ev.agent_type is None


def test_tool_use_from_a_subagent_says_whose_it_is():
    ev = translate_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "agent_id": "a8a5aea3b68767861",
            "agent_type": "Explore",
        }
    )
    assert isinstance(ev, AgentToolUse)
    assert (ev.agent_id, ev.agent_type) == ("a8a5aea3b68767861", "Explore")


def test_tool_result_from_a_subagent_says_whose_it_is():
    """分身自己也能再派分身；id 说的是「谁派的这一次」，也就是发出这条工具调用
    的那个线程。"""
    ev = translate_hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Task",
            "tool_response": "done",
            "agent_id": "outer-agent",
            "agent_type": "general-purpose",
        }
    )
    assert isinstance(ev, AgentToolResult)
    assert (ev.agent_id, ev.agent_type) == ("outer-agent", "general-purpose")


def test_message_and_stop_from_a_subagent_say_whose_they_are():
    message = translate_hook(
        {"hook_event_name": "MessageDisplay", "delta": "干完了", "agent_id": "w1"}
    )
    assert isinstance(message, AgentMessage)
    assert message.agent_id == "w1"

    result = translate_hook(
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "ok",
            "agent_id": "w1",
            "agent_type": "general-purpose",
        }
    )
    assert isinstance(result, AgentResult)
    assert (result.agent_id, result.agent_type) == ("w1", "general-purpose")


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
# 2.1.233 and 2.1.261, the device pin at the time): `message_id` is stable across the
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


def test_a_streamed_message_still_says_which_worker_said_it():
    """流式拼装是分身发言真正走的那条路——`translate_hook` 那条分支只在没有
    flush 字段的老 payload 上生效。标签必须穿过拼装层活下来，否则整条回复出来
    的时候没有主语，按 agent_id 归卡就永远漏掉分身说的话。"""
    asm = MessageAssembler()
    sub = {"agent_id": "worker-1", "agent_type": "general-purpose"}
    assert asm.add({**_flush("m1", 0, "查到三处\n", eid="e0"), **sub}) is None
    ev = asm.add({**_flush("m1", 1, "都在同一个文件里", final=True, eid="e1"), **sub})
    assert isinstance(ev, AgentMessage)
    assert ev.text == "查到三处\n都在同一个文件里"
    assert (ev.agent_id, ev.agent_type) == ("worker-1", "general-purpose")


def test_a_streamed_message_from_the_session_itself_has_no_worker():
    asm = MessageAssembler()
    assert asm.add(_flush("m1", 0, "先看代码。\n", eid="e0")) is None
    ev = asm.add(_flush("m1", 1, "再跑测试。", final=True, eid="e1"))
    assert isinstance(ev, AgentMessage)
    assert (ev.agent_id, ev.agent_type) == (None, None)


def test_a_later_flush_cannot_unname_the_worker():
    """乱序到达是常态（补录的 spool 文件会落在后面的 flush 之后）。第一条报出
    名字的 flush 说了算，后面缺这个 key 的 flush 不能把它抹掉——否则同一条消息
    归谁，取决于哪条 flush 碰巧先被处理。"""
    asm = MessageAssembler()
    named = {"agent_id": "worker-1", "agent_type": "general-purpose"}
    assert asm.add({**_flush("m1", 0, "前半句 ", eid="e0"), **named}) is None
    ev = asm.add(_flush("m1", 1, "后半句", final=True, eid="e1"))
    assert isinstance(ev, AgentMessage)
    assert ev.agent_id == "worker-1"


def test_a_drained_partial_keeps_the_worker_it_belonged_to():
    """屏幕死在一句话中间时，拼装器把收到的部分倒出来——倒出来的东西同样要
    带着它是谁说的。"""
    asm = MessageAssembler()
    asm.add(
        {
            **_flush("m1", 0, "只说了一半", eid="e0"),
            "agent_id": "worker-1",
            "agent_type": "Explore",
        }
    )
    drained = asm.drain()
    assert [(m.text, m.agent_id, m.agent_type) for m in drained] == [
        ("只说了一半", "worker-1", "Explore")
    ]


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


def test_stop_completes_its_partial_summary_before_it_reaches_chat():
    assembler = MessageAssembler()
    assembler.add(_flush("summary", 0, "Plan saved.\n", eid="first"))
    assembler.add(_flush("summary", 1, "- Sources checked.\n", eid="second"))
    full = "Plan saved.\n- Sources checked.\n- Locations need confirmation."
    events = assembler.translate(
        {"hook_event_name": "Stop", "last_assistant_message": full}
    )
    messages = [event for event in events if isinstance(event, AgentMessage)]
    assert len(messages) == 1
    assert messages[0].text == full
    assert messages[0].eids == ("first", "second")
    assert isinstance(events[-1], AgentResult)
    assert events[-1].text == messages[0].text
    assert (
        assembler.add(
            _flush(
                "summary", 2, "- Locations need confirmation.", final=True, eid="late"
            )
        )
        is None
    )


def test_display_after_stop_does_not_start_another_turn():
    assembler = MessageAssembler()
    assembler.translate({"hook_event_name": "Stop", "last_assistant_message": "Done."})
    assert assembler.translate(_flush("late", 0, "Done.", final=True)) == []
    assembler.translate({"hook_event_name": "UserPromptSubmit"})
    events = assembler.translate(_flush("next", 0, "Done.", final=True))
    assert len(events) == 1
    assert isinstance(events[0], AgentMessage)


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


# ---- 挂了的一步 ----
#
# 载荷形状取自 Claude Code 2.1.272 自己的 hook schema:
#   PostToolUseFailure{tool_name, tool_input, tool_use_id, error,
#                      is_interrupt?, duration_ms?}
# 失败走的是这个事件，不是 PostToolUse —— 后者的 schema 里根本没有 error 字段。


def _failure(**overrides):
    return translate_hook(
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_input": {"command": "pandoc a.md -o a.docx"},
            "tool_use_id": "toolu_1",
            "error": "bash: pandoc: command not found",
            "duration_ms": 12,
            **overrides,
        }
    )


def test_a_tool_call_carries_the_id_its_failure_will_name():
    ev = translate_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_use_id": "toolu_1",
            "tool_input": {"command": "pandoc a.md -o a.docx"},
        }
    )
    assert isinstance(ev, AgentToolUse)
    assert ev.call_id == "toolu_1"


def test_a_failed_tool_says_which_step_failed_and_why():
    ev = _failure()
    assert isinstance(ev, AgentStepFailed)
    assert ev.call_id == "toolu_1"
    assert ev.text == "bash: pandoc: command not found"


def test_a_tool_that_worked_reaches_the_room_through_its_effect_only():
    # 每一步的返回值都上报，等于把现场变成一份日志 —— 一次 Read 的返回值是整个
    # 文件。只有「挂了」是房间无法从效果看出来的。
    assert (
        translate_hook(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_use_id": "toolu_1",
                "tool_response": {"stdout": "ok", "stderr": ""},
            }
        )
        is None
    )


def test_someone_pressing_stop_is_not_a_tool_going_wrong():
    assert _failure(is_interrupt=True, error="Interrupted by user") is None


def test_a_long_failure_keeps_its_ending():
    # 命令在最后一行说它为什么不行，开头往往还是正常的编译日志。
    tail = "FAILED tests/test_x.py::test_y"
    ev = _failure(error="x " * 2000 + tail)
    assert ev.text.endswith(tail)
    assert len(ev.text) == STEP_ERROR_MAX


def test_a_subagent_conclusion_is_still_its_own_event():
    ev = translate_hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Task",
            "tool_use_id": "toolu_2",
            "tool_input": {"description": "查资料"},
            "tool_response": "查到了",
        }
    )
    assert isinstance(ev, AgentToolResult)
