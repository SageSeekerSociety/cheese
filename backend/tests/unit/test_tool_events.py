"""施工现场 tool events: display-time translation meta + 圆点分级 (platform vs
plain work). The platform rule is deterministic — tool-name prefix or a literal
`cheese <sub>` word pair in a Bash command — never natural-language guessing."""

from app.domain.agent.chat import (
    _format_tool_event,
    _is_platform_tool,
    _tool_event_meta,
)
from app.domain.agent.tool_preview import tool_detail, tool_preview


def _text(name: str, args: dict) -> str:
    """现场那一行，按生产路径拼出来：预览算一次，正文和 meta 都用它。"""
    return _format_tool_event(name, tool_preview(name, args))


def _meta(name: str, args: dict, *, platform: bool = False) -> dict:
    preview = tool_preview(name, args)
    return _tool_event_meta(
        name, preview, platform=platform, detail=tool_detail(name, args, preview)
    )


# ---- fallback text (baked into content for old clients / old rows) ----


def _verb(name: str, args: dict) -> str:
    return _text(name, args).split("\n", 1)[0]


def test_format_tool_event_translates_native_tools():
    for name, args, preview in (
        ("Glob", {"pattern": "**/*.py"}, "**/*.py"),
        ("Grep", {"pattern": "TODO"}, "TODO"),
        ("Agent", {"description": "查资料"}, "查资料"),
    ):
        verb, shown = _text(name, args).split("\n", 1)
        assert verb != name
        assert shown == preview
    # Task 是 Agent 的旧名，同一件事说同一句话。
    assert _verb("Task", {"description": "查"}) == _verb("Agent", {"description": "查"})


def test_format_tool_event_unknown_tool_falls_back_to_raw_name():
    # An unmapped (future) tool renders raw — the signal to extend the table.
    assert _text("FutureTool", {"x": 1}) == "FutureTool"


def test_format_tool_event_collapses_whitespace_and_truncates():
    out = _text("Bash", {"command": "make   a\t--flag " + "x" * 300})
    verb, preview = out.split("\n", 1)
    assert verb != "Bash"
    assert preview.startswith("make a --flag x")
    assert len(preview) <= 120


def test_a_multi_line_command_shows_the_step_that_does_something():
    # 换行在 shell 里就是分隔符。整条糊成一行，读的人得自己找哪句是重点。
    out = _text("Bash", {"command": "cd /tmp/somewhere\nmake check"})
    assert out == "执行命令\nmake check"


def test_a_recognised_command_borrows_the_fitting_verb():
    # meta.tool 仍然是 Bash（跑的确实是它），显示时按 action 说人话。
    shown = _text("Bash", {"command": "cat backend/app/main.py"})
    assert shown == f"{_verb('Read', {'file_path': 'x'})}\nbackend/app/main.py"


# ---- 圆点分级: platform detection ----


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
    assert _is_platform_tool("cheese_doc_set", {"path": "notes.md"})
    assert _is_platform_tool("cheese_accept_request", {"subject": "fix: x"})
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
        "detail": "grep -rn TODO backend/",
        "platform": False,
    }


def test_tool_event_meta_omits_as_tool_when_nothing_fits_better():
    assert "as_tool" not in _meta("Bash", {"command": "make release"})


def test_tool_event_meta_never_borrows_the_platform_action_key():
    # meta.action 已经归平台动作卡所有（它答的是「这张卡指向哪个资源」）。现场
    # 的动词覆盖挤进同一个键，卡片就会指向一个叫 "Grep" 的资源。
    assert "action" not in _meta("Bash", {"command": "grep -rn TODO backend/"})


# ---- pi 原生工具 ----


def test_format_tool_event_translates_pis_tools_too():
    # 同一件事在两个 harness 里说同一句话。
    assert _text("read", {"path": "hello.py"}) == (
        f"{_verb('Read', {'file_path': 'x'})}\nhello.py"
    )
    assert _text("write", {"path": "notes.md", "content": "x"}) == (
        f"{_verb('Write', {'file_path': 'x'})}\nnotes.md"
    )
    assert (
        _text("grep", {"pattern": "TODO"}) == f"{_verb('Grep', {'pattern': 'x'})}\nTODO"
    )
    verb, shown = _text("ls", {"path": "backend"}).split("\n", 1)
    assert verb != "ls"
    assert shown == "backend"


def test_a_platform_action_is_one_wherever_the_cheese_cli_runs():
    # pi 房间的 shell 工具叫 bash —— 认不出它，机器上的 cheese 命令就没有琥珀点。
    assert _is_platform_tool("bash", {"command": "cheese show output/x.docx"})
    assert not _is_platform_tool("bash", {"command": "echo cheese"})


def test_meta_carries_the_argument_as_it_was_written():
    # 一行写的是「写入文件 · report.md」，摊开要能看见真正跑的那条命令。
    command = "cat > report.md <<'EOF'\n# 标题\nEOF"
    meta = _meta("bash", {"command": command})
    assert meta["arg"] == "report.md"
    assert meta["detail"] == command


def test_meta_omits_the_second_copy_when_the_line_already_says_it_all():
    assert "detail" not in _meta("Bash", {"command": "make test"})


def test_the_amber_dot_is_judged_on_the_line_it_sits_next_to():
    # 一行写着 `gh pr list`，点却因为命令别处有个 $(cheese gh-token) 而发亮 ——
    # 读的人看到的是两件对不上的事，而琥珀色本该只说一件：这一步改了项目的东西。
    command = "GH_TOKEN=$(cheese gh-token 2>/dev/null) gh pr list"
    assert _text("Bash", {"command": command}) == "执行命令\ngh pr list"
    assert not _is_platform_tool("Bash", {"command": command})


def test_the_platform_step_is_the_one_the_line_shows():
    command = "make && cheese sync"
    assert _text("Bash", {"command": command}) == "执行命令\ncheese sync"
    assert _is_platform_tool("Bash", {"command": command})
