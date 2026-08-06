"""施工现场 tool events: display-time translation meta + 圆点分级 (platform vs
plain work). The platform rule is deterministic — tool-name prefix or a literal
`cheese <sub>` word pair in a Bash command — never natural-language guessing."""

from app.domain.agent.chat import (
    _format_tool_event,
    _is_platform_tool,
    _tool_event_meta,
)

# ---- fallback text (baked into content for old clients / old rows) ----


def test_format_tool_event_translates_native_tools():
    assert _format_tool_event("Glob", {"pattern": "**/*.py"}) == "找文件\n**/*.py"
    assert _format_tool_event("Grep", {"pattern": "TODO"}) == "搜内容\nTODO"
    agent = _format_tool_event("Agent", {"description": "查资料"})
    assert agent == "派分身去查\n查资料"
    assert _format_tool_event("Task", {"description": "查"}) == "派分身去查\n查"


def test_format_tool_event_unknown_tool_falls_back_to_raw_name():
    # An unmapped (future) tool renders raw — the signal to extend the table.
    assert _format_tool_event("FutureTool", {"x": 1}) == "FutureTool"


def test_format_tool_event_collapses_whitespace_and_truncates():
    out = _format_tool_event("Bash", {"command": "echo   a\n\tb " + "x" * 300})
    verb, preview = out.split("\n", 1)
    assert verb == "执行命令"
    assert preview.startswith("echo a b x")
    assert len(preview) <= 120


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
    meta = _tool_event_meta("Grep", {"pattern": "TODO"}, platform=False)
    assert meta == {"tool": "Grep", "arg": "TODO", "platform": False}


def test_tool_event_meta_omits_empty_arg():
    meta = _tool_event_meta("FutureTool", {"x": 1}, platform=False)
    assert meta == {"tool": "FutureTool", "platform": False}
