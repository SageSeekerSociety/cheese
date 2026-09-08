"""施工现场 tool events: display-time translation meta + 圆点分级 (platform vs
plain work). The platform rule is deterministic — tool-name prefix or a literal
`cheese <sub>` word pair in a Bash command — never natural-language guessing."""

from app.domain.agent.chat import (
    _format_tool_event,
    _is_platform_tool,
    _tool_event_meta,
)
from app.domain.agent.tool_preview import tool_preview


def _text(name: str, args: dict) -> str:
    """现场那一行，按生产路径拼出来：预览算一次，正文和 meta 都用它。"""
    return _format_tool_event(name, tool_preview(name, args))


def _meta(name: str, args: dict, *, platform: bool = False) -> dict:
    return _tool_event_meta(name, tool_preview(name, args), platform=platform)


# ---- fallback text (baked into content for old clients / old rows) ----


def test_format_tool_event_translates_native_tools():
    assert _text("Glob", {"pattern": "**/*.py"}) == "找文件\n**/*.py"
    assert _text("Grep", {"pattern": "TODO"}) == "搜内容\nTODO"
    assert _text("Agent", {"description": "查资料"}) == "派分身去查\n查资料"
    assert _text("Task", {"description": "查"}) == "派分身去查\n查"


def test_format_tool_event_unknown_tool_falls_back_to_raw_name():
    # An unmapped (future) tool renders raw — the signal to extend the table.
    assert _text("FutureTool", {"x": 1}) == "FutureTool"


def test_format_tool_event_collapses_whitespace_and_truncates():
    out = _text("Bash", {"command": "make   a\t--flag " + "x" * 300})
    verb, preview = out.split("\n", 1)
    assert verb == "执行命令"
    assert preview.startswith("make a --flag x")
    assert len(preview) <= 120


def test_a_multi_line_command_shows_the_step_that_does_something():
    # 换行在 shell 里就是分隔符。整条糊成一行，读的人得自己找哪句是重点。
    out = _text("Bash", {"command": "cd /tmp/somewhere\nmake check"})
    assert out == "执行命令\nmake check"


def test_a_recognised_command_borrows_the_fitting_verb():
    # meta.tool 仍然是 Bash（跑的确实是它），显示时按 action 说人话。
    assert _text("Bash", {"command": "cat backend/app/main.py"}) == (
        "读文件\nbackend/app/main.py"
    )


# ---- 圆点分级: platform detection ----


def test_cheese_mcp_tool_is_platform():
    assert _is_platform_tool("mcp__cheese__update_doc", {"content": "x"})
    assert _is_platform_tool("mcp__cheese__remember", {})


def test_bash_with_cheese_cli_is_platform():
    assert _is_platform_tool("Bash", {"command": 'cheese title "新标题"'})
    assert _is_platform_tool("Bash", {"command": "/usr/local/bin/cheese doc set"})
    # cheese appearing PAST the 120-char preview cut still counts: platform is
    # decided on the full command, not the truncated display preview.
    assert _is_platform_tool("Bash", {"command": "x" * 200 + " && cheese notify hi"})


def test_plain_work_is_not_platform():
    assert not _is_platform_tool("Bash", {"command": "ls -la"})
    # trailing "cheese" with no subcommand word is not a CLI call
    assert not _is_platform_tool("Bash", {"command": "echo cheese"})
    assert not _is_platform_tool("Grep", {"pattern": "cheese title"})
    assert not _is_platform_tool("Read", {"file_path": "/tmp/cheese title.txt"})
    assert not _is_platform_tool("Agent", {"description": "去查 cheese 的用法"})


# ---- structured meta persisted on event blocks ----


def test_tool_event_meta_carries_tool_arg_platform():
    assert _meta("Grep", {"pattern": "TODO"}) == {
        "tool": "Grep",
        "arg": "TODO",
        "platform": False,
    }


def test_tool_event_meta_omits_empty_arg():
    assert _meta("FutureTool", {"x": 1}) == {"tool": "FutureTool", "platform": False}


def test_tool_event_meta_records_what_ran_and_how_to_label_it():
    # 「跑了什么」和「怎么称呼它」是两个字段，不是一个 —— 现场显示成「读文件」，
    # 但这一行的确是一次 Bash 调用，追起来要能看出来。
    assert _meta("Bash", {"command": "grep -rn TODO backend/"}) == {
        "tool": "Bash",
        "as_tool": "Grep",
        "arg": "TODO",
        "platform": False,
    }


def test_tool_event_meta_omits_as_tool_when_nothing_fits_better():
    assert "as_tool" not in _meta("Bash", {"command": "make release"})


def test_tool_event_meta_never_borrows_the_platform_action_key():
    # meta.action 已经归平台动作卡所有（它答的是「这张卡指向哪个资源」）。现场
    # 的动词覆盖挤进同一个键，卡片就会指向一个叫 "Grep" 的资源。
    assert "action" not in _meta("Bash", {"command": "grep -rn TODO backend/"})
