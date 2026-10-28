"""施工现场 tool events: display-time translation meta + 圆点分级 (platform vs
plain work). The platform rule is deterministic — tool-name prefix or a literal
`cheese <sub>` word pair in a Bash command — never natural-language guessing."""

from app.domain.session_record.preview import (
    _is_platform_tool,
)


def test_cheese_mcp_tool_is_platform():
    assert _is_platform_tool("mcp__cheese__update_doc", {"content": "x"})
    assert _is_platform_tool("mcp__cheese__remember", {})


def test_the_mcp_prefix_is_read_off_the_server_that_is_actually_there():
    """服务器叫 `native`，而探测以前只认 `mcp__cheese__`。

    claude_code 的 MCP 服务器注册名是 `native`（`remote_execution/client.py` 写
    mcp.json 时 `servers = {"native": ...}`），所以每个平台工具的真实名字是
    `mcp__native__…`。只认旧拼法时这一格既不算平台动作、前端的标签表也剥不掉前缀 ——
    时间线上原样渲染 `mcp__native__cheese_feedback_propose` 配一颗中性点，而这和
    「这个工具本来就没有标签」在屏幕上是同一件事，谁也不会报。
    """
    assert _is_platform_tool("mcp__native__cheese_feedback_propose", {"title": "x"})
    assert _is_platform_tool("mcp__native__chat_send", {"content": "x"})
    assert _is_platform_tool(
        "mcp__native__platform_request", {"method": "GET", "path": "/x"}
    )


def test_the_transport_under_native_is_not_a_platform_action():
    """`mcp__native__` 底下**不都是**平台动作。

    `invoke` 是这个 harness 搬运读写与命令的通道（Read / Edit / Bash 都从它过），
    把它一起算成平台动作，时间线上会在「只是读了一个文件」旁边点一颗琥珀色的点。
    """
    assert not _is_platform_tool("mcp__native__invoke", {"tool": "Read"})
    assert not _is_platform_tool("Read", {"file_path": "x"})


def test_a_platform_command_called_as_a_tool_is_platform():
    """The same action, under the name a harness without MCP publishes it as.

    A pi room registers the platform's tools as extension tools, bare-named, so
    missing them would put the neutral dot on every step that changed something
    outside the machine — the one distinction the dot exists to draw, drawn
    backwards, in the rooms where it mattered.
    """
    assert _is_platform_tool("cheese_doc", {"action": "set", "content": "# 实况"})
    assert _is_platform_tool("cheese_card", {"action": "request", "subject": "fix: x"})
    # The alias the room's own system prompt names on every turn.
    assert _is_platform_tool("chat_send", {"content": "第一版好了"})


def test_bash_with_cheese_cli_is_platform():
    assert _is_platform_tool("Bash", {"command": "cheese worktree 1234"})
    assert _is_platform_tool("Bash", {"command": "/usr/local/bin/cheese sync"})
    # A cheese segment past the 120-char preview cut still counts — and 现场
    # shows that segment, so the dot and the line say the same thing.
    assert _is_platform_tool("Bash", {"command": "x" * 200 + " && cheese push-fix"})


def test_plain_work_is_not_platform():
    assert not _is_platform_tool("Bash", {"command": "ls -la"})
    # trailing "cheese" with no subcommand word is not a CLI call
    assert not _is_platform_tool("Bash", {"command": "echo cheese"})
    assert not _is_platform_tool("Grep", {"pattern": "cheese title"})
    assert not _is_platform_tool("Read", {"file_path": "/tmp/cheese title.txt"})
    assert not _is_platform_tool("Agent", {"description": "去查 cheese 的用法"})
    # A background job is the agent working on the machine, like any command.
    assert not _is_platform_tool("bash_start", {"command": "pnpm dev"})


# ---- structured meta persisted on event blocks ----


def test_a_platform_action_is_one_wherever_the_cheese_cli_runs():
    # pi 房间的 shell 工具叫 bash —— 认不出它，机器上的 cheese 命令就没有琥珀点。
    assert _is_platform_tool("bash", {"command": "cheese show output/x.docx"})
    assert not _is_platform_tool("bash", {"command": "echo cheese"})


def test_a_call_recorded_under_a_retired_name_is_still_a_platform_action():
    """`chat_edit` carries no `cheese_` prefix; it is `cheese_chat(action=edit)`
    now, and a record made under the old name was a platform action then too."""
    assert _is_platform_tool("chat_edit", {"message_id": "m1", "content": "改过"})
    assert _is_platform_tool("mcp__native__chat_edit", {"message_id": "m1"})
    assert not _is_platform_tool("Edit", {"file_path": "a.md"})
