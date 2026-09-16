"""施工现场那一行里，中文动词后面跟着的那截文本。

动词本身是显示时翻译的（前端按 ``meta.as_tool`` / ``meta.tool`` 查自己的表），所以
这里只回答一个问题：这次工具调用，最值得给人看的是哪一截，怎么写才读得懂。

三条规则，各自针对一种读不懂：

- **Bash 按「中文说明 → 中文模板 → 英文说明 → 命令原文」四档往下退。** Claude
  Code 的 Bash 工具本来就要求填 ``description``，一句话说明这条命令在干什么 ——
  它就是现成的现场文案，之前被整个丢掉了。但它多半是英文写的，所以英文的那份
  不直接用：先让下面的解析器试一次，认得出来就走中文模板（既是中文又更准），
  认不出来才退回那句英文 —— 它仍然胜过一行 shell。命令原文是最后一档：绝对路径
  开头的 ``cd`` 一段就能把预览占满，读的人看不出这一步在干什么。
- **文件路径剪成工作区相对路径。** 设备上的 checkout 一律在
  ``…/.cheese/work/<project>/<topic>/`` 下，这个前缀比预览的长度上限还长，不剪
  掉的话「读取文件」后面跟的是一串 uuid，看不出读的是哪个文件。
- **解析命令时先把没信息量的前置段落剥掉**（``cd``、``export``、开头的
  ``VAR=值``）。剩下那段的头一个词认得出来的，连动词一起换成更贴切的那个
  （``action``）—— 认不出来就原样显示，不猜：动词说错比英文更难读。
- **一条命令里有 cheese CLI 那一段时，显示的就是那一段。** ``make && cheese doc
  set`` 做的两件事里，房间要看见的是后一件：它改的是这个项目的东西，不只是这台
  机器上的文件。圆点也判在同一段上（``cheese_subcommand``）—— 判断和显示咬在一
  起，才不会出现「这一行写着 make，点却是琥珀色的」。

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

#: 摊开那一行之后的长度上限。一行管的是扫，这一份管的是看 —— 一条命令连着
#: heredoc 能有几千字符，而摊开的人要的正是被剪掉的那截。真的封顶时末尾留一个
#: 省略号，读的人自己就看得出来还有。
DETAIL_MAX = 4000

#: 「这句话是中文吗」—— 有没有汉字就够了，不需要语言识别库。判错的代价只是多走
#: 一次解析器，而解析器认不出来时又会退回原文，两头都不会把话说坏。
_HAS_CHINESE = re.compile(r"[一-鿿]")

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
    # pi 原生工具。名字全小写，参数名也自成一套，和上面那批没有一个重合 —— 漏掉
    # 一个的后果不是报错，是现场那一行后面什么都不跟，看不出这步在动哪个文件。
    "bash": "command",
    "read": "path",
    "write": "path",
    "edit": "path",
    "ls": "path",
    "find": "pattern",
    "grep": "pattern",
}

#: 参数是一条 shell 命令的工具 —— 命令要拆开读，不是照着参数名取一截就完事。
SHELL_TOOLS = frozenset({"Bash", "bash"})

#: 只是在看这条路径的工具。写一份 SKILL.md 是在写技能，读一份是在照着它干活 ——
#: 两件事不能显示成同一句。
_READ_TOOLS = frozenset({"Read", "read"})

#: 一份技能就是一个目录加一份 ``SKILL.md``（``agent/skills.py`` 发到机器上的就是
#: 这个形状）。名字在目录上，文件名对每一份技能都一样。
_SKILL_FILE = "SKILL.md"


def _skill_name(raw: object) -> str:
    """这条路径指的是哪份技能；不是技能文件时是空的。

    读 ``…/skills/documents/SKILL.md`` 这一步，说成「读取文件 · SKILL.md」等于
    什么都没说 —— 每份技能的文件名都叫这个，而这一步真正发生的是芝士开始按
    documents 这份说明干活。Claude Code 有个 Skill 工具，房间里本来就这么显示；
    pi 和 Codex 没有，它们是直接把文件读进去的，于是同一件事在两种房间里长得不
    一样。
    """
    parts = [p for p in _collapse(raw).replace("\\", "/").split("/") if p]
    if len(parts) > 1 and parts[-1] == _SKILL_FILE:
        return parts[-2]
    return ""


# 参数本身就是一条路径的工具 —— 这些要剪工作区前缀。
_PATH_TOOLS = frozenset(
    {
        "Read",
        "Edit",
        "Write",
        "NotebookEdit",
        "write_file",
        "read",
        "edit",
        "write",
        "ls",
    }
)


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
#:
#: 值里的 ``$(...)`` 要整个算进来，否则 ``T=$(cheese gh-token 2>/dev/null)`` 会
#: 在第一个空格处断开，现场显示的是 ``gh-token 2>/dev/null)`` —— 半截替换出来的
#: 残句，不是任何人写过的命令。同理，最后一个赋值后面允许什么都不跟：整段只有
#: 赋值时它该被当成没信息量而跳过，而不是原样显示一行 ``root=/home/…``。
_LEADING_ASSIGNMENTS = re.compile(
    r"""^(?:[A-Za-z_][A-Za-z0-9_]*="""
    r"""(?:'[^']*'|"[^"]*"|\$\([^)]*\)|`[^`]*`|[^\s'"`$]+|\$)*"""
    r"""(?:\s+|$))+"""
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

#: ``>`` / ``>>``，前面可以带一个文件描述符号，目标可以贴着写。``2>&1`` 和
#: ``&>`` 不算：它们指的是另一个描述符，不是一个能显示出来的文件。
_REDIRECT_RE = re.compile(r"^\d?>>?$|^\d?>>?(?P<target>[^&>].*)$")

#: 内容从别处来、只负责把它落到重定向目标里的命令。这几个配上重定向就是「写
#: 文件」，写的是目标那个文件 —— ``cat > x.py <<'EOF'`` 是 agent 落脚本的常用
#: 写法，按 ``cat`` 的字面显示成「读文件」，读的人看到的是它没做过的事。
#: 表只收这几个：``python3 s.py > out.log`` 做的是跑脚本，把它说成写 out.log
#: 同样是说错。
_WRITE_HEADS = frozenset({"cat", "tee", "echo", "printf"})

#: 重定向到这里等于扔掉，不是产出，别把它当成写出来的文件。
_DISCARD = "/dev/null"


def _redirect_target(tokens: list[str]) -> str:
    """这一段把输出写去哪个文件；没有重定向，或写去的不是文件时是空的。"""
    for index, token in enumerate(tokens):
        match = _REDIRECT_RE.match(token)
        if match is None:
            continue
        target = match.group("target")
        if target is None:
            target = tokens[index + 1] if index + 1 < len(tokens) else ""
        if target and not target.startswith(("&", "-")):
            return target
    return ""


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


#: 机器上那个平台 CLI。段首是它，这一段就是一次平台动作。
CHEESE_CLI = "cheese"


def _chosen_segment(command: str) -> tuple[str, list[str]]:
    """这条命令在现场显示的是哪一段。

    平台动作优先，其余取第一段有信息量的。返回显示用的原文（引号照留）和切好的
    词 —— 两者分开，才不会为了认出命令而把它显示成一句它没写过的话。
    """
    first: tuple[str, list[str]] = ("", [])
    for segment in _split_segments(command):
        text = _LEADING_ASSIGNMENTS.sub("", segment).strip()
        if not text:
            continue
        tokens = _tokenize(text)
        if not tokens or _head_word(tokens) in _NOISE_HEADS:
            continue
        if _head_word(tokens) == CHEESE_CLI:
            return text, tokens
        if not first[1]:
            first = (text, tokens)
    return first


def cheese_subcommand(command: str) -> str:
    """现场显示的那一段跑的是哪个 cheese 子命令；不是平台动作时是空的。

    判断和显示咬在同一段上。按整条命令找 ``cheese`` 两个字会让一行写着
    ``gh pr list``、圆点却是琥珀色的 —— 因为命令别处有个
    ``T=$(cheese gh-token)``。读的人看到的是两件对不上的事，而琥珀色本该只说
    一件：这一步改了这个项目的东西。
    """
    _, tokens = _chosen_segment(command)
    if len(tokens) > 1 and _head_word(tokens) == CHEESE_CLI:
        return tokens[1]
    return ""


def command_preview(command: str, *, work_dir: str = "") -> ToolPreview:
    """一条 shell 命令在现场怎么写。

    说明是英文、或者压根没写说明时都走这里。``action`` 非空表示认出来了，调用方
    可以据此决定要不要用它顶掉那句英文。
    """
    meaningful, tokens = _chosen_segment(command)
    if not meaningful:
        # 整条命令都是准备动作（``cd x && export Y=1``）—— 没有更好的说法了，
        # 原样显示，别把它说成一件它不是的事。
        return ToolPreview(_collapse(command)[:PREVIEW_MAX])

    head = _head_word(tokens)
    redirect = _redirect_target(tokens)
    if redirect:
        # 重定向改的是这条命令在做什么，所以它先于头一个词。认得出是在落文件就
        # 说落的哪个，认不出就原样显示 —— 按头一个词查表会把写说成读。
        if head in _WRITE_HEADS and redirect != _DISCARD:
            return ToolPreview(short_path(redirect, work_dir=work_dir), "Write")
        return ToolPreview(_collapse(meaningful)[:PREVIEW_MAX])
    action = _ACTION_BY_HEAD.get(head)
    if action is None:
        return ToolPreview(_collapse(meaningful)[:PREVIEW_MAX])

    operand = _first_operand(head, tokens)
    skill = _skill_name(operand) if action == "Read" else ""
    if skill:
        return ToolPreview(skill, "Skill")
    if action == "Grep":
        text = _collapse(operand)
    else:
        text = short_path(operand, work_dir=work_dir)
    if not text:
        # 认出了动词但没有操作数（``ls``、``find .``）—— 动词照换，后面跟命令
        # 本身，比留空更说明问题。
        return ToolPreview(_collapse(meaningful)[:PREVIEW_MAX], action)
    return ToolPreview(text[:PREVIEW_MAX], action)


def tool_detail(name: str, args: dict, preview: ToolPreview) -> str:
    """摊开这一行时给人看的那一份：参数原文。

    和预览分开算，是因为两者要的东西相反 —— 预览要短、要重写（``cat > x.py
    <<EOF`` 说成「写文件 x.py」才读得懂），而摊开的人要的恰恰是被重写掉、被剪掉
    的原文。原文不剪路径、不折行、不换说法，只封顶。

    和那一行说的一样时返回空：摊开之后看见同一句话，等于什么也没摊开。
    """
    if not isinstance(args, dict):
        return ""
    key = "command" if name in SHELL_TOOLS else _TOOL_ARG.get(name)
    if key is None or args.get(key) is None:
        return ""
    text = str(args[key]).strip()
    if not text or _collapse(text) == preview.text:
        return ""
    if len(text) > DETAIL_MAX:
        return text[:DETAIL_MAX] + "…"
    return text


def tool_preview(name: str, args: dict, *, work_dir: str = "") -> ToolPreview:
    """一次工具调用在现场怎么显示。"""
    if not isinstance(args, dict):
        return ToolPreview()
    if name in SHELL_TOOLS:
        # pi 的 bash 没有 description 这个参数，所以它总是走下面的解析那条路 ——
        # 四档退让本来就是为「只有一行命令」写的，不必为它再分一支。
        described = _collapse(args.get("description") or "")
        if described and _HAS_CHINESE.search(described):
            return ToolPreview(described[:PREVIEW_MAX])
        # 英文的说明不直接显示：先让解析器试一次，认出来就走中文模板。一句英文
        # 句子比命令原文好读，但比「读取文件 · backend/app/main.py」差 —— 后者
        # 既是中文又更准。认不出来才退回那句英文，它仍然胜过一行 shell。
        parsed = command_preview(str(args.get("command") or ""), work_dir=work_dir)
        if parsed.action is not None:
            return parsed
        if described:
            return ToolPreview(described[:PREVIEW_MAX])
        return parsed
    key = _TOOL_ARG.get(name)
    if key is None or args.get(key) is None:
        return ToolPreview()
    skill = _skill_name(args[key]) if name in _READ_TOOLS else ""
    if skill:
        return ToolPreview(skill, "Skill")
    if name in _PATH_TOOLS:
        return ToolPreview(short_path(args[key], work_dir=work_dir))
    return ToolPreview(_collapse(args[key])[:PREVIEW_MAX])
