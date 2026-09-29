"""现场事件行那一块搬出来之后，自己站得住：

- 直接 import 新模块就能测，不经过 ``ChatService``、不碰数据库（这个文件不
  import chat，也不起任何 app fixture）；
- 搬走的名字在 ``app.domain.agent.chat`` 上仍然导得出来 —— 那是兼容门面，
  既有调用点与测试不用改一行；
- 新模块不反向 import chat，否则门面就成了循环。

行为本身（正文怎么拼、meta 有哪些键、圆点怎么分级）由 ``test_tool_events.py``
与 ``test_turn_change_summary.py`` 从门面那条路径覆盖；这里补的是搬出来之后
新出现的两个东西：模块边界，和原先没有直接单测的分身回吐。
"""

import ast
import pathlib

import pytest

from app.domain.agent import chat, event_lines

# ---- 门面：搬走的名字是同一个对象，导入路径没变 ----


def test_the_moved_names_are_the_same_objects_behind_the_facade():
    for name in (
        "_CHANGE_FILES_LISTED",
        "_change_summary_meta",
        "_Changeset",
        "_diff_file_stats",
        "_format_change_summary",
        "_format_tool_event",
        "_is_platform_tool",
        "_short_tool_name",
        "_subagent_event_text",
        "_subagent_result_meta",
        "_tool_event_meta",
    ):
        assert getattr(chat, name) is getattr(event_lines, name), name


def test_the_module_does_not_import_the_facade_back():
    """反向 import 就是循环，门面也就不是门面了。"""
    tree = ast.parse(pathlib.Path(event_lines.__file__).read_text())
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    modules |= {
        alias.name
        for n in ast.walk(tree)
        if isinstance(n, ast.Import)
        for alias in n.names
    }
    assert "app.domain.agent.chat" not in modules


# ---- 分身回吐（原先没有直接的单测） ----


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


# ---- 其余名字从这个模块直接可用 ----


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
