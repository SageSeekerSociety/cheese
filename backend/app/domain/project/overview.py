"""项目目标：项目自己的一份文档，每段对话里的 AI 队友都读它。

它只写三件事：这个项目要做成什么、什么时候做成、这阶段不做什么。人和芝士都可以
改；综合的概览最上面显示它。代码是什么归仓库的 README，给 AI 的规矩归仓库的
CLAUDE.md，都不写在这里。项目现在在做什么也不写在这里：进行中的任务在各频道的
概览里，芝士要看就用工具去查，不必每轮往每段对话塞一份项目快照。

本模块是纯函数：给同样的文字，永远得到同样的注入内容。
"""

import re

#: 写作预算（字符）。超了照样写入，只警告（见 `doc_checks`）——但注入时按这个
#: 数截断，因为它是**人写的**、上限没有结构保证。
BRIEF_CHAR_BUDGET = 1500

_HEADING_RE = re.compile(r"(?m)^[ \t]*(#{1,6})[ \t]*(.+?)[ \t]*$")
#: 写成项目目标之前，这份文档按小节写，注入的只有其中「项目是什么」那一节（目标、
#: 范围、对外口径）。已有的文档照原样留着，所以仍然认得出这一节。
#: 标题里的序号（①、1.、一、）不算标题本身。
_BRIEF_TITLES = ("项目是什么", "项目简介", "项目概览")
_ORDINAL_RE = re.compile(r"^[\s①②③④⑤⑥⑦⑧⑨⑩0-9一二三四五六七八九十.、,)]+")


def render_overview(*, brief: str) -> str:
    """注入用的项目目标。

    空着时说「还没写」并给一句要写什么——留白读起来像「这个项目没有目标」，
    而事实只是没人写过。
    """
    unwritten = (
        "（还没写。这里写这个项目要做成什么、什么时候做成、这阶段不做什么，"
        f"≤{BRIEF_CHAR_BUDGET} 字。）"
    )
    return brief or unwritten


def _sections(content: str) -> list[tuple[int, str, str]]:
    """``(级别, 标题, 正文)``，按出现顺序。纯文本切分，不认代码围栏——实况
    文档的标题不该长在代码块里。"""
    heads = list(_HEADING_RE.finditer(content))
    out: list[tuple[int, str, str]] = []
    for i, match in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(content)
        body = content[match.end() : end].strip()
        out.append((len(match.group(1)), match.group(2).strip(), body))
    return out


def _is_titled(title: str, names: tuple[str, ...]) -> bool:
    bare = _ORDINAL_RE.sub("", title).strip()
    return bare in names


def project_brief(content: str) -> str:
    """这份文档里注入的那部分。

    按小节写的老文档只取「项目是什么」那一节；认不出这一节时给回**全文**——
    写成项目目标的文档没有这一节，整篇都是目标；而老文档也不能因为标题对不上就
    在提示词里整个消失，静默丢掉项目共识，比多注入几行糟得多。
    """
    for _level, title, body in _sections(content):
        if _is_titled(title, _BRIEF_TITLES):
            return body
    return content.strip()
