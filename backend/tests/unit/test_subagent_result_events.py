"""分身回吐: a subagent's conclusion has to reach the room.

The room already showed 「派分身去查 X」 (the PreToolUse hook). What it could not
show was the answer — the subagent reports to whoever spawned it and its
transcript dies with the container, so a subagent that confidently returned
nonsense looked exactly like one that nailed it. These tests cover the path that
carries the conclusion out: PostToolUse → AgentToolResult → 现场 event text.
"""

from app.domain.agent.chat import (
    _SUBAGENT_RESULT_MAX,
    _subagent_event_text,
    _subagent_result_meta,
)
from app.domain.agent.harness.claude_code.hook_events import translate_hook
from app.domain.agent.service import (
    AgentToolResult,
)


def test_subagent_post_tool_use_becomes_a_tool_result():
    ev = translate_hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Task",
            "tool_input": {"description": "查分页接口现状"},
            "tool_response": {"content": [{"type": "text", "text": "用的是 offset"}]},
            "_eid": "e-1",
        }
    )
    assert isinstance(ev, AgentToolResult)
    assert ev.name == "Task"
    assert ev.description == "查分页接口现状"
    assert ev.text == "用的是 offset"
    assert ev.eid == "e-1"


def test_agent_is_the_same_tool_under_its_newer_name():
    ev = translate_hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "tool_response": "结论",
        }
    )
    assert isinstance(ev, AgentToolResult)
    assert ev.name == "Agent"
    assert ev.text == "结论"


def test_plain_tools_return_nothing_to_the_room():
    """Only the subagent tools. A Read's return value is the whole file and a
    Bash's is already on screen — surfacing those would double the timeline."""
    for tool in ("Bash", "Read", "Edit", "Grep"):
        assert (
            translate_hook(
                {
                    "hook_event_name": "PostToolUse",
                    "tool_name": tool,
                    "tool_response": "some output",
                }
            )
            is None
        )


def test_response_shapes_the_cli_has_actually_used():
    """Shape-tolerant on purpose: a payload we cannot read is indistinguishable
    in the room from a subagent that returned nothing."""
    shapes = [
        "plain string",
        ["plain string"],
        [{"type": "text", "text": "plain string"}],
        {"content": [{"type": "text", "text": "plain string"}]},
        {"text": "plain string"},
        {"output": "plain string"},
    ]
    for response in shapes:
        ev = translate_hook(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Task",
                "tool_response": response,
            }
        )
        assert isinstance(ev, AgentToolResult), response
        assert ev.text == "plain string", response


def test_unreadable_or_empty_response_lands_nothing():
    for response in (None, "", "   ", {}, [], {"content": []}, 42):
        assert (
            translate_hook(
                {
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Task",
                    "tool_response": response,
                }
            )
            is None
        ), response


def test_event_text_names_the_question_and_the_answer():
    text = _subagent_event_text("查分页接口现状", "结论：用的是 offset\n分页在路由层")
    first, second = text.split("\n")
    assert first == "分身查完了：查分页接口现状"
    # Collapsed to one line — a 现场 event renders as a single row.
    assert second == "结论：用的是 offset 分页在路由层"


def test_event_text_without_a_description_still_reads():
    assert _subagent_event_text("", "结论") == "分身查完了\n结论"


def test_long_conclusions_are_capped_so_the_room_stays_readable():
    """不刷屏: a subagent can return thousands of words; the room gets a bounded
    summary and the transcript keeps the rest."""
    huge = "很长的结论。" * 500
    text = _subagent_event_text("查一个事实", huge)
    body = text.split("\n", 1)[1]
    assert len(body) == _SUBAGENT_RESULT_MAX + 1  # + the ellipsis
    assert body.endswith("…")

    meta = _subagent_result_meta("Task", "查一个事实", huge)
    assert meta["subagent"]["truncated"] is True
    assert len(meta["subagent"]["summary"]) == _SUBAGENT_RESULT_MAX


def test_short_conclusions_are_not_marked_truncated():
    meta = _subagent_result_meta("Task", "查一个事实", "结论")
    assert meta["subagent"] == {
        "tool": "Task",
        "description": "查一个事实",
        "summary": "结论",
        "truncated": False,
    }
    # 圆点分级: the subagent's work is 芝士's own, not a platform action.
    assert meta["platform"] is False


def test_no_meta_tool_key_so_todays_frontend_renders_the_text():
    """The UI translates `meta.tool` through its own verb table and renders an
    unknown name raw. Leaving the key off routes this block down the
    content-text path, which reads correctly with no frontend change."""
    assert "tool" not in _subagent_result_meta("Task", "d", "r")
