"""项目总览：项目自己的一份文档，每段对话里的 AI 队友都读它。

它写的是这个项目是什么（目标、范围、对外口径），人和芝士都可以改；综合的概览
最上面显示它。项目现在在做什么不写在这里：进行中的任务在各频道的概览里，芝士
要看就用工具去查，不必每轮往每段对话塞一份项目快照。

本模块是纯函数：给同样的文字，永远得到同样的注入内容。
"""

import re

#: 注入时那一节的标题，也是总览里认得出的「项目是什么」那一块。
BRIEF_TITLE = "项目是什么"

#: 写作预算（字符）。超了照样写入，只警告（见 `doc_checks`）——但注入时按这个
#: 数截断，因为它是**人写的**、上限没有结构保证。
BRIEF_CHAR_BUDGET = 1500

_HEADING_RE = re.compile(r"(?m)^[ \t]*(#{1,6})[ \t]*(.+?)[ \t]*$")
#: 标题里的序号（①、1.、一、）不算标题本身。
_BRIEF_TITLES = ("项目是什么", "项目简介", "项目概览")
_ORDINAL_RE = re.compile(r"^[\s①②③④⑤⑥⑦⑧⑨⑩0-9一二三四五六七八九十.、,)]+")


def render_overview(*, brief: str) -> str:
    """注入用的项目总览。

    空着时说「还没写」并给一句要写什么——留白读起来像「这个项目没有目标」，
    而事实只是没人写过。
    """
    unwritten = (
        f"（还没写。这里写目标、范围（做/不做）、对外口径，≤{BRIEF_CHAR_BUDGET} 字。）"
    )
    return f"## {BRIEF_TITLE}\n\n" + (brief or unwritten)


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
    """总览里「项目是什么」那一块的正文。

    认不出这一块时给回**全文**：老项目的总览写的是「大家都该知道的」那些小节，
    不能因为标题对不上就在提示词里整个消失——静默丢掉项目共识，比多注入几行糟
    得多。
    """
    for _level, title, body in _sections(content):
        if _is_titled(title, _BRIEF_TITLES):
            return body
    return content.strip()
