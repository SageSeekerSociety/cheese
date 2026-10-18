"""现场事件行：分身回吐的那一行，和从 ``event_lines`` 直接调用的平台判定与改动行。

正文怎么拼、meta 有哪些键、圆点怎么分级，另由 ``test_tool_events.py`` 与
``test_turn_change_summary.py`` 从 ``ChatService`` 那条路径覆盖。
"""

import pytest

from app.domain.agent import event_lines

# ---- 分身回吐 ----


def test_a_subagent_conclusion_is_one_capped_line():
    out = event_lines._subagent_event_text(
        "查分页接口现状", "结论：\n分页用的是 offset"
    )
    # 结论里的换行糊成一个空格：这一行是一行，不是一段。
    assert out == "分身查完了：查分页接口现状\n结论： 分页用的是 offset"


def test_a_long_conclusion_is_cut_and_says_so_in_meta():
    """不刷屏: 几千字的分身结论不能整段糊进时间线。"""
    result = "字" * (event_lines._SUBAGENT_RESULT_MAX + 100)
    text = event_lines._subagent_event_text("查一下", result)
    assert text.endswith("…")
    assert len(text.split("\n", 1)[1]) == event_lines._SUBAGENT_RESULT_MAX + 1

    meta = event_lines._subagent_result_meta("Task", "查一下", result)["subagent"]
    assert len(meta["summary"]) == event_lines._SUBAGENT_RESULT_MAX
    assert meta["truncated"] is True


def test_a_short_conclusion_is_not_marked_truncated():
    meta = event_lines._subagent_result_meta("Task", "查一下", "好了")["subagent"]
    assert meta == {
        "tool": "Task",
        "description": "查一下",
        "summary": "好了",
        "truncated": False,
    }


def test_a_subagent_with_nothing_to_say_still_says_it_spoke():
    assert event_lines._subagent_event_text("", "") == "分身查完了"


def test_the_subagent_meta_carries_no_tool_key_for_the_room():
    """meta.tool 会被前端的动词表翻译，而这张表的键是工具名。

    分身这条不是平台动作，也不该让 UI 拿 ``Task`` 去查一句人话 —— 所以结构化
    字段挂在 ``subagent`` 底下，块子上没有 ``tool``。
    """
    meta = event_lines._subagent_result_meta("Task", "查一下", "好了")
    assert meta["platform"] is False
    assert "tool" not in meta


# ---- 平台判定与改动行 ----


@pytest.mark.parametrize(
    "name,args",
    [
        ("mcp__native__chat_send", {"content": "x"}),
        ("Bash", {"command": "cheese worktree 1234"}),
        ("chat_send", {"content": "x"}),
    ],
)
def test_platform_grading_is_reachable_from_the_module(name, args):
    assert event_lines._is_platform_tool(name, args)


def test_the_change_line_is_reachable_from_the_module():
    files = event_lines._diff_file_stats(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    assert files == [{"path": "a.py", "added": 1, "removed": 1}]
    line = event_lines._format_change_summary(files)
    assert line == "这一轮改了 1 个文件（+1 -1）\na.py"
    assert (
        event_lines._change_summary_meta(event_lines._Changeset([], files))[
            "changeset"
        ]["commit"]
        is None
    )
