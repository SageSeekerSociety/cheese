"""现场事件行的正文与结构化 meta —— 纯函数，不碰数据库、不碰轮次状态。

时间线上每一行的形状都定在这里，因为同一件事有两份说法时，两份会各说各话：

- 一次工具调用（`_format_tool_event` 的兜底正文 + `_tool_event_meta` 的结构化
  负载，两者由同一份 ``ToolPreview`` 推导）；
- 一颗琥珀点算不算平台动作（`_is_platform_tool`，按工具名前缀或命令里那句
  ``cheese`` 判，不从自然语言猜）；
- 一个分身回吐的结论（`_subagent_event_text` / `_subagent_result_meta`）；
- 一轮的改动汇总（`_diff_file_stats` / `_format_change_summary` /
  `_change_summary_meta`）。

调它们的是落库那几步（`_persist_tool_event`、`_persist_subagent_result`、
`_persist_change_summary`、`_turn_changeset`），它们现在住在 `room_events.py`。
搬出来时按原样搬——这些函数的
入参出参就是它们与调用方之间全部的约定，所以行为一格没动。
"""

import codecs
import re
from dataclasses import dataclass

from app.core.sentences import say
from app.domain.agent.tool_preview import SHELL_TOOLS, ToolPreview, cheese_subcommand

# 施工现场: render each tool call like a Claude Code action line — a Chinese verb
# plus a short preview of its most telling argument. Stored in the event block as
# "verb\npreview" (preview omitted when empty).
_TOOL_VERB = {
    "update_doc": "更新文档",
    "remember": "记入项目记忆",
    "notify": "发送通知",
    "request_accept": "提交审阅",
    "pin_milestone": "添加里程碑",
    "write_file": "写入文件",
    "record_decision": "记录决策",
}


# Native Claude Code tools (sandbox mode) → 现场 labels. Systematic: every tool
# the agent can invoke has a Chinese verb + its most telling argument as the
# preview; an unmapped (future) tool falls back to its raw name, which is the
# signal to extend this table.
_TOOL_VERB.update(
    {
        "Bash": "执行命令",
        "Write": "写入文件",
        "Edit": "修改文件",
        "Read": "读取文件",
        "Glob": "查找文件",
        "Grep": "搜索内容",
        "WebSearch": "搜索网页",
        "WebFetch": "读取网页",
        "Agent": "派出分身",
        "Task": "派出分身",  # older CLI name for Agent
        "NotebookEdit": "修改笔记本",
        "TodoWrite": "更新任务清单",
        "BashOutput": "查看命令输出",
        "KillShell": "终止命令",
        "KillBash": "终止命令",
        "ExitPlanMode": "提交方案待确认",
        "AskUserQuestion": "向用户提问",
        "Skill": "调用技能",
        "ToolSearch": "查找工具",
    }
)

# pi 原生工具。同样的动词，另一套名字 —— 两个 harness 的工具名没有一个重合，所以
# 少了这几行，一个 pi 房间的现场从头到尾只有 bash / read / write 这些英文原名。
_TOOL_VERB.update(
    {
        "bash": "执行命令",
        "read": "读取文件",
        "write": "写入文件",
        "edit": "修改文件",
        "ls": "列出目录",
        "find": "查找文件",
        "grep": "搜索内容",
    }
)


def _format_tool_event(name: str, preview: ToolPreview) -> str:
    """Human-readable FALLBACK text for an event block (old clients / old rows).

    The UI renders from the structured meta (see _tool_event_meta); this baked
    string only shows when meta is absent. Both are built from the SAME
    ToolPreview, so the baked line and the rendered one cannot describe the
    call differently."""
    verb = _TOOL_VERB.get(preview.action or name, name)
    return f"{verb}\n{preview.text}" if preview.text else verb


# 现场圆点分级: a PLATFORM action (amber dot) vs plain work (neutral dot).
# Deterministic by construction — tool-name prefix, or the `cheese` CLI at the
# head of the command segment 现场 displays. NEVER inferred from natural
# language, and never from the word appearing somewhere else in the command:
# the dot and the text on that line have to be about the same thing.
#: 平台动作在各个 harness 里叫什么。同一件事三种拼法，因为把工具交给模型的机制
#: 各不相同：MCP 服务器自己加前缀，pi 那边的目录是从 CLI 的命令树生成的，而
#: `chat_send` 是系统提示每一轮都在点名、于是 extension 额外注册的那个别名。
#: MCP 那个前缀（`mcp__<服务器>__<工具>`）也要认。claude_code 的服务器注册名是
#: `native`（见 `harness/claude_code/remote_execution/client.py` 写 mcp.json 时的
#: `servers = {"native": ...}`），所以真实名字长这样：
#: `mcp__native__cheese_feedback_propose`；
#: `mcp__cheese__` 是这套东西还叫 cheese 时的拼法，仍然认（历史行还躺在库里）。
#: 只认后者会让整件事**静默失效**：前缀认不出来 → 这一格既不算平台动作、中文标签也
#: 查不到，于是时间线上原样渲染 `mcp__native__…` 配一个中性点，而它看着完全正常。
_PLATFORM_PREFIXES = ("mcp__cheese__", "mcp__native__", "cheese_")
#: ……但 `mcp__native__` 底下**不都是平台动作**：`invoke` 是这个 harness 搬运读写与
#: 命令的通道（Read / Edit / Bash 都从它过），把它算成平台动作会在时间线上点一颗琥珀
#: 色的点 —— 而那只是读了一个文件。
_NOT_A_PLATFORM_TOOL = frozenset({"mcp__native__invoke"})
#: 名字里没有 `cheese_` 的那几个平台工具：`chat_send`、`chat_edit` 和 `todo_write`
#: 的名字照模型已经认得的说法起；另外两个是平台自己的 MCP 工具。
_PLATFORM_ALIASES = frozenset(
    {"chat_send", "chat_edit", "todo_write", "platform_request", "send_user_file"}
)

#: `mcp__<服务器>__<工具>` 的前缀。**认服务器名，不认某一个写死的**：写死一个的话，
#: 服务器改名那一天这里会静态地失效，而失效的样子和时间线正常的样子一模一样。
_MCP_PREFIX = re.compile(r"^mcp__[a-z0-9_]+__")


def _short_tool_name(raw_name: str) -> str:
    """把 MCP 工具名归一成模型看到的那个（`mcp__native__chat_send` → `chat_send`）。"""
    return _MCP_PREFIX.sub("", raw_name)


def _is_platform_tool(raw_name: str, args: dict) -> bool:
    """True when the tool call is a platform action: a cheese tool under any of
    the names a harness publishes it as, or a shell command that invokes the
    machine's `cheese` CLI."""
    if raw_name in _NOT_A_PLATFORM_TOOL:
        return False
    if raw_name.startswith(_PLATFORM_PREFIXES):
        return True
    if _short_tool_name(raw_name) in _PLATFORM_ALIASES:
        return True
    if raw_name in SHELL_TOOLS and isinstance(args, dict):
        return bool(cheese_subcommand(str(args.get("command", ""))))
    return False


def _tool_event_meta(
    name: str, preview: ToolPreview, *, platform: bool, detail: str = ""
) -> dict:
    """Structured payload persisted on an event block: the UI translates the
    tool name and colors the dot from these fields at DISPLAY time, so a verb
    missing from today's table is never baked in untranslated forever.

    ``as_tool`` rides alongside ``tool`` rather than replacing it: ``tool`` says
    what actually ran, ``as_tool`` says whose label reads better (a Bash
    `cat foo.py` is still a Bash call, but 「读取文件」 is what it did). NOT named
    ``action`` — that key already means "which platform resource this card points
    at" (see the frontend's platformNotice), and one name answering two questions
    is how a card ends up pointing at a resource called "Read".

    ``detail`` is the argument as it was actually written, for the reader who
    opens the line. It is stored NEXT TO ``arg`` rather than replacing it
    because the two want opposite things: ``arg`` is rewritten and cut to stay
    scannable on one line, and what the opener came for is exactly what that
    rewriting removed. Only the preview is ever computed from it, so a line
    with nothing more to say carries no second copy."""
    meta: dict = {"tool": name, "platform": platform}
    if preview.text:
        meta["arg"] = preview.text
    if preview.action:
        meta["as_tool"] = preview.action
    if detail:
        meta["detail"] = detail
    return meta


# 分身回吐 (§9 可见性): a subagent reports to whoever spawned it and nothing else,
# so the room used to see 「派出分身 X」 and never the answer. Its conclusion
# lands as its own 现场 event — CAPPED, because the room is a place people read:
# a subagent can return thousands of words and pasting them here would bury the
# conversation instead of informing it. The full text is in the transcript; what
# the room needs is enough to tell "it answered the question" from "it went off
# the rails", which is the whole point of making it visible.
_SUBAGENT_RESULT_MAX = 500


def _subagent_event_text(description: str, result: str) -> str:
    """现场 line for a returning subagent: "分身查完了：<问题>\\n<结论摘要>".

    NOTE the deliberate absence of ``meta.tool`` on the block this text goes on
    (see _subagent_result_meta): the UI translates meta.tool through its own verb
    table, and a name that table doesn't know renders raw. Leaving it off routes
    this block down the content-text path, which reads correctly with no frontend
    change — while the structured fields ride along for when there is one.
    """
    summary = " ".join(result.split())
    if len(summary) > _SUBAGENT_RESULT_MAX:
        summary = summary[:_SUBAGENT_RESULT_MAX] + "…"
    head = " ".join(description.split())[:80]
    verb = say("subagentDoneAbout", task=head) if head else say("subagentDone")
    return say("subagentDoneSummary", verb=verb, summary=summary) if summary else verb


def _subagent_result_meta(name: str, description: str, result: str) -> dict:
    """Structured payload for a subagent conclusion.

    ``truncated`` is what tells a future UI that an 「展开」 affordance has
    something behind it, instead of it having to compare lengths against a
    constant that lives on the other side of the wire.
    """
    summary = " ".join(result.split())
    return {
        "platform": False,  # the agent's own work, not a platform action
        "subagent": {
            "tool": name,
            "description": " ".join(description.split())[:120],
            "summary": summary[:_SUBAGENT_RESULT_MAX],
            "truncated": len(summary) > _SUBAGENT_RESULT_MAX,
        },
    }


# 一轮的改动汇总 (§8.4 commit 可视化): what this turn did to the topic branch, as
# ONE event at turn end. Bounded on purpose — the ask was "改了几个文件、大致
# 增删量，能点开看 diff", NOT the diff body: a 2000-line diff pasted into the
# timeline is the definition of 刷屏, and the diff panel already renders it well.
_CHANGE_FILES_LISTED = 12  # paths named in the event's text and in meta.files

# "diff --git a/<old> b/<new>", with git's quoting when a path needs it. The
# b-side is the path AFTER the change, which is where a rename should be filed.
_DIFF_HEADER_RE = re.compile(r'^diff --git "?a/.+?"? "?b/(?P<path>.+?)"?$')


@dataclass
class _Changeset:
    """What one turn did to the topic branch."""

    commits: list[str]  # newest first; commits[0] is what a UI opens
    files: list[dict]


def _diff_file_stats(diff: str) -> list[dict]:
    """Per-file added/removed line counts parsed out of a unified diff.

    Counted from the diff text rather than asked of git with ``--numstat``
    because the only workspace readers available here return diff bodies; the
    parse is the cheaper half of that trade (the body is already in memory).

    Content lines are counted only INSIDE a hunk. Skipping "+++"/"---" by prefix
    instead would silently drop a deleted line whose own text starts with "--".
    """
    stats: list[dict] = []
    current: dict | None = None
    in_hunk = False
    for line in diff.splitlines():
        header = _DIFF_HEADER_RE.match(line)
        if header is not None:
            path = header.group("path")
            if line.endswith('"'):
                # Git quotes UTF-8 bytes with C-style octal escapes.
                path = codecs.escape_decode(path.encode())[0].decode()
            current = {"path": path, "added": 0, "removed": 0}
            stats.append(current)
            in_hunk = False
            continue
        if current is None:
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:
            continue  # index / mode / rename / ---+++ headers
        if line.startswith("+"):
            current["added"] += 1
        elif line.startswith("-"):
            current["removed"] += 1
    return stats


def _format_change_summary(files: list[dict]) -> str:
    """现场 line for a turn's changes: a headline plus the paths it touched."""
    added = sum(f["added"] for f in files)
    removed = sum(f["removed"] for f in files)
    head = say("changeSummary", count=len(files), added=added, removed=removed)
    listed = " · ".join(f["path"] for f in files[:_CHANGE_FILES_LISTED])
    rest = len(files) - min(len(files), _CHANGE_FILES_LISTED)
    if rest > 0:
        return say("changeSummaryFilesMore", head=head, files=listed, rest=rest)
    return say("changeSummaryFiles", head=head, files=listed) if listed else head


def _change_summary_meta(changeset: _Changeset) -> dict:
    """Structured payload for a turn's change summary.

    ``commit`` is the newest of the turn's commits and the ref a UI should open:
    ``GET /api/projects/{project}/git/diff?ref=<commit>`` already serves exactly
    that diff, so 「点开看 diff」 needs no new endpoint.
    """
    files = changeset.files
    return {
        "platform": True,  # the platform's own bookkeeping, not something 芝士 did
        "changeset": {
            "commit": changeset.commits[0] if changeset.commits else None,
            "commits": changeset.commits,
            "files_total": len(files),
            "added": sum(f["added"] for f in files),
            "removed": sum(f["removed"] for f in files),
            "files": files[:_CHANGE_FILES_LISTED],
            "files_omitted": max(0, len(files) - _CHANGE_FILES_LISTED),
        },
    }
