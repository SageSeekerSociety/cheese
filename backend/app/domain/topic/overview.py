"""项目总览的实况文档：哪一块是谁写的。

总览文档是项目**根话题**那一份实况文档，全项目共看（结论 7）。它长成五块：

    ① 项目是什么      人 / 芝士写，≤1500 字（目标、范围、对外口径）
    ② 现在在做什么    每个活跃话题一行
    ③ 最近决策        决策卡，最近 10 条
    ④ 里程碑
    ⑤ 已结束的话题    结论卡一行 + 话题链接

**只有 ① 在文档本体里。** ②~⑤ 由平台从结构化数据现拼，注入的那一刻才存在，
谁的 `cheese_doc_set` 也覆盖不到它们。

这是这一版改造的要点。以前 ②~⑤ 靠人 / 芝士手抄进文档：抄进去的是**过程**，
而过程没人敢删（删了链接就断），于是文档只涨不落，最后靠读取时截断把垃圾藏起
来。改成现拼以后，这几块的篇幅由**条数**决定，不靠截断，也抄不进正文——往文档
里写它们，写的是一个读者根本不会读到的副本。

本模块是纯函数：给同样的结构化输入，永远得到同样的文字。取数在调用方
（`agent/chat.py` 的 `_overview_text` / `TopicService`），这样渲染能直接对着测。
"""

import re

#: ① 的标题。总览文档里 agent 写的那一块，认的就是它。
BRIEF_TITLE = "项目是什么"

#: ① 的写作预算（字符）。超了照样写入，只警告（见 `doc_checks`）——但注入时
#: 按这个数截断，因为它是**人写的**、上限没有结构保证。
BRIEF_CHAR_BUDGET = 1500

#: 自动区各按条数截断。条数上限就是篇幅上限。
ACTIVE_TOPICS_LIMIT = 20
DECISIONS_LIMIT = 10
MILESTONES_LIMIT = 10
CLOSED_TOPICS_LIMIT = 10

#: 一行里一句话的长度上限；超了截断加省略号。列表是索引，不是正文。
_LINE_CHARS = 80

_HEADING_RE = re.compile(r"(?m)^[ \t]*(#{1,6})[ \t]*(.+?)[ \t]*$")
#: 标题里的序号（①、1.、一、）不算标题本身。
_BRIEF_TITLES = ("项目是什么", "项目简介", "项目概览")
_ORDINAL_RE = re.compile(r"^[\s①②③④⑤⑥⑦⑧⑨⑩0-9一二三四五六七八九十.、,)]+")

#: 话题文档模板里「当前结论」那一块（`doc_form.md` 与提示词同源）。
CONCLUSION_TITLES = ("当前结论", "结论")
_SENTENCE_END_RE = re.compile(r"[。！？!?；;]|\.\s")


def render_overview(*, brief: str, auto: str) -> str:
    """注入用的整份总览：① 的正文，加上（只在总览房间里）②~⑤。

    ① 空着时说「还没写」并给一句要写什么——留白读起来像「这个项目没有目标」，
    而事实只是没人写过。
    """
    unwritten = (
        f"（还没写。这里写目标、范围（做/不做）、对外口径，≤{BRIEF_CHAR_BUDGET} 字。）"
    )
    head = f"## {BRIEF_TITLE}\n\n" + (brief or unwritten)
    return head + (f"\n\n{auto}" if auto else "")


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
    """总览文档第 ① 块「项目是什么」的正文。

    认不出这一块时给回**全文**：一份还没按新结构写过的总览（老项目里有「大家都
    该知道的」那些小节），不能因为标题对不上就在提示词里整个消失——静默丢掉
    项目共识，比多注入几行糟得多。
    """
    for _level, title, body in _sections(content):
        if _is_titled(title, _BRIEF_TITLES):
            return body
    return content.strip()


def topic_conclusion(content: str) -> str | None:
    """一份话题文档的「当前结论」第一句，没有就是 None。

    注入到总览 ② / ⑤ 的那一句。取不到就报 None——编一个「进行中」出来，比
    留空更容易被当成事实。
    """
    for _level, title, body in _sections(content):
        if _is_titled(title, CONCLUSION_TITLES):
            return first_sentence(body)
    return None


def first_sentence(text: str) -> str | None:
    """第一行里第一句，压缩成一行。空正文给 None。"""
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*> ").strip()
        if not line:
            continue
        match = _SENTENCE_END_RE.search(line)
        sentence = line[: match.end()] if match else line
        return _clip(sentence.rstrip())
    return None


def _clip(text: str, limit: int = _LINE_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _row(parts: list[str | None], sep: str = " · ") -> str:
    return sep.join(p for p in parts if p)


def _block(title: str, rows: list[str]) -> str:
    """一块自动区；一行都没有的块整个不出现。"""
    if not rows:
        return ""
    return f"{title}\n" + "\n".join(f"- {r}" for r in rows)


def _active_row(topic: dict) -> str:
    conclusion = topic.get("conclusion")
    return _row(
        [
            f"<#{topic['id']}> {topic['title']}",
            f"负责人：{topic['owner']}" if topic.get("owner") else None,
            topic.get("status") or None,
            f"当前结论：{conclusion}" if conclusion else "当前结论：（没写）",
        ]
    )


def _closed_row(topic: dict) -> str:
    conclusion = topic.get("conclusion")
    return _row(
        [
            f"<#{topic['id']}> {topic['title']}",
            f"结论：{conclusion}" if conclusion else "结论：（没写）",
        ]
    )


def _decision_row(decision: dict) -> str:
    # 决策卡的正文是要点，不是一句话；这里只取前 120 字，全文在那张卡上。
    return _row(
        [
            _clip(decision["text"], 120),
            f"（<#{decision['topic_id']}> {decision['topic']}）"
            if decision.get("topic")
            else None,
        ]
    )


def _milestone_row(milestone: dict) -> str:
    return _row(
        [
            milestone["title"],
            f"截止 {milestone['due']}" if milestone.get("due") else "没定截止日期",
            milestone.get("status") or None,
        ]
    )


def render_overview_auto(
    *,
    active_topics: list[dict],
    decisions: list[dict],
    milestones: list[dict],
    closed_topics: list[dict],
) -> str:
    """②~⑤ 拼成一段 markdown；四块都空时给空串。

    每块的输入是**已经取好的结构化行**（见模块顶部的字段约定），所以这里只排版、
    不查库。空块整块不出现：一份「## 最近决策（暂无）」对读者是噪音，对这个项目
    有没有决策这件事毫无帮助。
    """
    blocks = [
        _block(
            "## 现在在做什么",
            [_active_row(t) for t in active_topics[:ACTIVE_TOPICS_LIMIT]],
        ),
        _block(
            "## 最近决策",
            [_decision_row(d) for d in decisions[:DECISIONS_LIMIT]],
        ),
        _block(
            "## 里程碑",
            [_milestone_row(m) for m in milestones[:MILESTONES_LIMIT]],
        ),
        _block(
            "## 已结束的话题",
            [_closed_row(t) for t in closed_topics[:CLOSED_TOPICS_LIMIT]],
        ),
    ]
    blocks = [b for b in blocks if b]
    if not blocks:
        return ""
    return (
        "以下四块由平台从结构化数据现拼（话题、决策卡、里程碑、结论卡），"
        "不在本文档正文里，也不要往正文里抄：\n\n" + "\n\n".join(blocks)
    )
