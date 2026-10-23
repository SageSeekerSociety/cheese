"""现场事件行：分身回吐的那一行，和从 ``event_lines`` 直接调用的平台判定与改动行。

正文怎么拼、meta 有哪些键、圆点怎么分级，另由 ``test_tool_events.py`` 与
``test_turn_change_summary.py`` 从 ``ChatService`` 那条路径覆盖。
"""

import pytest

from app.domain.agent import event_lines
from app.domain.session_record.preview import _is_platform_tool

# ---- 分身回吐 ----


@pytest.mark.parametrize(
    "name,args",
    [
        ("mcp__native__chat_send", {"content": "x"}),
        ("Bash", {"command": "cheese worktree 1234"}),
        ("chat_send", {"content": "x"}),
    ],
)
def test_platform_grading_is_reachable_from_the_module(name, args):
    assert _is_platform_tool(name, args)


def test_the_change_line_is_reachable_from_the_module():
    files = event_lines._diff_file_stats(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    assert files == [{"path": "a.py", "added": 1, "removed": 1}]
    line = event_lines._format_change_summary(files)
    assert line == "这次改了 1 个文件（+1 -1）\na.py"
    assert (
        event_lines._change_summary_meta(event_lines._Changeset([], files))[
            "changeset"
        ]["commit"]
        is None
    )
