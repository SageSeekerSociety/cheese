"""施工现场那一行里，中文动词后面跟着的那截文本。

动词本身是显示时翻译的（前端按 ``meta.as_tool`` / ``meta.tool`` 查自己的表），所以
这里只回答一个问题：这次工具调用，最值得给人看的是哪一截，怎么写才读得懂。

三条规则，各自针对一种读不懂：

- **Bash 优先用模型自己写的 ``description``**，而不是命令原文。Claude Code 的
  Bash 工具本来就要求填这个字段，一句话说明这条命令在干什么 —— 它就是现成的
  现场文案，之前被丢掉了。命令原文顶不上它：绝对路径开头的 ``cd`` 一段就能把
  预览占满，读的人看不出这一步在干什么。
- **文件路径剪成工作区相对路径。** 设备上的 checkout 一律在
  ``…/.cheese/work/<project>/<topic>/`` 下，这个前缀比预览的长度上限还长，不剪
  掉的话「读取文件」后面跟的是一串 uuid，看不出读的是哪个文件。
- **没有 description 的 Bash，把没信息量的前置段落剥掉**（``cd``、``export``、
  开头的 ``VAR=值``）。剩下那段的头一个词认得出来的，顺带把动词也换成更贴切的
  那个（``action``）—— 认不出来就原样显示，不猜：动词说错比英文更难读。

``action`` 只是「按哪个工具的标签来显示」，``tool`` 仍然如实记录真正跑的是哪个
工具。两者分开，是因为「跑了什么」和「怎么称呼它」是两个问题，合成一个字段就
只能牺牲其中一个。
"""

from __future__ import annotations

import re
import shlex
import uuid
from dataclasses import dataclass

#: 预览的长度上限。现场是一行，不是一段。
PREVIEW_MAX = 120

#: 每个设备 checkout 共享的那截路径。``device_provider._work_dir`` 拼的是同一个
#: 形状；那边没有改成引用这里，是因为剪不掉前缀并不会出错 —— 走的是下面按段保留
#: 的退路，照样读得懂。为一个不影响正确性的字面量，让设备侧反过来依赖一个管显示
#: 文案的模块，不划算。
_WORK_ROOT = ".cheese/work"

#: 工作区之外的绝对路径（/tmp、$HOME…）保留末尾几段。长到这个数才动手，短路径
#: 完整显示比截断的更有用。
_TAIL_TRIGGER = 48
_TAIL_SEGMENTS = 2


def work_subpath(project_id: uuid.UUID, topic_id: uuid.UUID) -> str:
    """一个话题的 checkout 相对于家目录的位置。"""
    return f"{_WORK_ROOT}/{project_id}/{topic_id}"


@dataclass(frozen=True)
class ToolPreview:
    """一次工具调用在现场怎么显示。

    ``action`` 是可选的动词覆盖 —— 值是「标签更贴切的那个工具名」，不是新造的
    词，这样前端那张现成的工具标签表不用为它加条目。
    """

    text: str = ""
    action: str | None = None


# 每个工具身上最能说明问题的那个参数。缺席的工具只显示动词。
_TOOL_ARG = {
    # cheese 平台动作
    "update_doc": "content",
    "remember": "fact",
    "notify": "title",
    "request_accept": "reviewer_handle",
    "pin_milestone": "title",
    "write_file": "path",
    "record_decision": "decision",
    # Claude Code 原生工具
    "Bash": "command",
    "Write": "file_path",
    "Edit": "file_path",
    "Read": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
    "WebSearch": "query",
    "WebFetch": "url",
    "Agent": "description",
    "Task": "description",
    "NotebookEdit": "notebook_path",
    "Skill": "skill",
    "ToolSearch": "query",
}

# 参数本身就是一条路径的工具 —— 这些要剪工作区前缀。
_PATH_TOOLS = frozenset({"Read", "Edit", "Write", "NotebookEdit", "write_file"})


def _collapse(value: object) -> str:
    return " ".join(str(value).split())


def short_path(raw: object, *, work_dir: str = "") -> str:
    """一条路径在现场怎么写：工作区里的写相对路径，工作区外的留末尾几段。"""
    path = _collapse(raw).replace("\\", "/")
    if not path:
        return ""
    if work_dir:
        base = work_dir.rstrip("/")
        cut = path.find(base)
        if cut >= 0:
            # 工作区根目录本身后面什么都没有 —— 说一声「.」，别留一个空串。
            return path[cut + len(base) :].lstrip("/") or "."
    if len(path) > _TAIL_TRIGGER and "/" in path:
        parts = [p for p in path.split("/") if p]
        if len(parts) > _TAIL_SEGMENTS:
            return "…/" + "/".join(parts[-_TAIL_SEGMENTS:])
    return path[:PREVIEW_MAX]


# ---- 没有 description 的 Bash：把命令剥到剩下有信息量的那段 ----

#: 单独成段时不值得显示的命令。它们要么是在为下一段做准备（cd/export），要么
#: 是在收尾（true），本身不说明这一步在干什么。
_NOISE_HEADS = frozenset({"cd", "export", "set", "unset", "source", ".", "true", ":"})

#: 段首的 ``VAR=值`` 是给后面那条命令用的环境，不是命令本身。
_LEADING_ASSIGNMENTS = re.compile(
    r"""^(?:[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|"[^"]*"|\S*)\s+)+"""
)

#: 头一个词 → 标签更贴切的那个工具。只收录操作数位置没有歧义的命令：认错动词
#: 比留着英文更难读，所以这张表宁可短。
_ACTION_BY_HEAD = {
    "cat": "Read",
    "head": "Read",
    "tail": "Read",
    "less": "Read",
    "bat": "Read",
    "grep": "Grep",
    "rg": "Grep",
    "ls": "Glob",
    "tree": "Glob",
    "find": "Glob",
    "fd": "Glob",
}

#: 会吃掉下一个词的选项，按命令分开列 —— ``-n`` 在 head 里带数字、在 grep 里是
#: 开关，一张共用的表必然把其中一个弄错。
_VALUE_FLAGS = {
    "head": {"-n", "-c"},
    "tail": {"-n", "-c"},
    "grep": {"-e", "-m", "-A", "-B", "-C", "--include", "--exclude"},
    "rg": {"-e", "-m", "-A", "-B", "-C", "-g", "--glob", "--type", "-t"},
    "find": {"-maxdepth", "-mindepth", "-type", "-newer"},
    "fd": {"-t", "--type", "-d", "--max-depth"},
}

#: find 的第一个操作数是搜索起点（多半是 ``.``），真正说明问题的是 -name 的值。
_NAME_FLAGS = {"-name", "-iname", "-path", "-ipath"}


def _split_segments(command: str) -> list[str]:
    """按 ``;`` ``&&`` ``||`` 换行切段，引号里的分隔符不算。

    管道不切：``grep x | head`` 是一件事，切开只会把它说成两件。
    """
    segments: list[str] = []
    buf: list[str] = []
    quote = ""
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(command):
                buf.append(command[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch in ";\n":
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        if command.startswith("&&", i) or command.startswith("||", i):
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return [s.strip() for s in segments if s.strip()]


def _tokenize(segment: str) -> list[str]:
    """按 shell 的规矩切词：引号里的空格不算分隔，引号本身不进操作数。

    引号不配对时 ``shlex`` 会抛错 —— 那说明这条命令我们本来也读不懂，退回按空白
    切，让它走「认不出来就原样显示」那条路。
    """
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _head_word(tokens: list[str]) -> str:
    """段首那个词，去掉它的路径前缀：``/usr/local/bin/cheese`` → ``cheese``。"""
    if not tokens:
        return ""
    return tokens[0].rsplit("/", 1)[-1]


def _first_operand(head: str, tokens: list[str]) -> str:
    """跳过选项和它们的值，取第一个真正的操作数。"""
    value_flags = _VALUE_FLAGS.get(head, frozenset())
    prefer_name = head in {"find", "fd"}
    if prefer_name:
        # find 的第一个操作数是搜索起点（多半是 ``.``），说明不了问题；真正在找
        # 什么写在 -name 后面。
        for index, token in enumerate(tokens):
            if token in _NAME_FLAGS and index + 1 < len(tokens):
                return tokens[index + 1]
    skip = False
    for token in tokens[1:]:
        if skip:
            skip = False
            continue
        if token in value_flags or (prefer_name and token in _NAME_FLAGS):
            skip = True
            continue
        if token.startswith("-"):
            continue
        if token:
            return token
    return ""


def command_preview(command: str, *, work_dir: str = "") -> ToolPreview:
    """一条 shell 命令在现场怎么写（模型没写 description 时的退路）。"""
    meaningful = ""
    tokens: list[str] = []
    for segment in _split_segments(command):
        # 显示用原文（引号照留），分类用切好的词 —— 两者分开，才不会为了认出
        # 命令而把它显示成一句它没写过的话。
        text = _LEADING_ASSIGNMENTS.sub("", segment).strip()
        if not text:
            continue
        segment_tokens = _tokenize(text)
        if not segment_tokens or _head_word(segment_tokens) in _NOISE_HEADS:
            continue
        meaningful, tokens = text, segment_tokens
        break
    if not meaningful:
        # 整条命令都是准备动作（``cd x && export Y=1``）—— 没有更好的说法了，
        # 原样显示，别把它说成一件它不是的事。
        return ToolPreview(_collapse(command)[:PREVIEW_MAX])

    head = _head_word(tokens)
    action = _ACTION_BY_HEAD.get(head)
    if action is None:
        return ToolPreview(_collapse(meaningful)[:PREVIEW_MAX])

    operand = _first_operand(head, tokens)
    if action == "Grep":
        text = _collapse(operand)
    else:
        text = short_path(operand, work_dir=work_dir)
    if not text:
        # 认出了动词但没有操作数（``ls``、``find .``）—— 动词照换，后面跟命令
        # 本身，比留空更说明问题。
        return ToolPreview(_collapse(meaningful)[:PREVIEW_MAX], action)
    return ToolPreview(text[:PREVIEW_MAX], action)


def tool_preview(name: str, args: dict, *, work_dir: str = "") -> ToolPreview:
    """一次工具调用在现场怎么显示。"""
    if not isinstance(args, dict):
        return ToolPreview()
    if name == "Bash":
        described = _collapse(args.get("description") or "")
        if described:
            return ToolPreview(described[:PREVIEW_MAX])
        return command_preview(str(args.get("command") or ""), work_dir=work_dir)
    key = _TOOL_ARG.get(name)
    if key is None or args.get(key) is None:
        return ToolPreview()
    if name in _PATH_TOOLS:
        return ToolPreview(short_path(args[key], work_dir=work_dir))
    return ToolPreview(_collapse(args[key])[:PREVIEW_MAX])
