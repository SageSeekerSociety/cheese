"""现场事件行：分身回吐的那一行是平台自己的。

正文怎么拼、meta 有哪些键、圆点怎么分级，另由 ``test_tool_events.py`` 从
``ChatService`` 那条路径覆盖。
"""

import pytest

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
