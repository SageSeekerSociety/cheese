"""项目总览的实况文档：哪一块是谁写的。

总览文档是项目**根话题**那一份实况文档，全项目共看（结论 7）。它长成三块：

    ① 项目是什么      人 / 芝士写，≤1500 字（目标、范围、对外口径）
    ② 现在在做什么    每个活跃话题一行
    ③ 已结束的话题    结论卡一行 + 话题链接

**只有 ① 在文档本体里。** ②③ 由平台从结构化数据现拼，注入的那一刻才存在，
谁的 `cheese_doc_set` 也覆盖不到它们。

这是这一版改造的要点。以前 ②③ 靠人 / 芝士手抄进文档：抄进去的是**过程**，
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
CLOSED_TOPICS_LIMIT = 10

#: 一行里一句话的长度上限；超了截断加省略号。列表是索引，不是正文。
_LINE_CHARS = 80

_HEADING_RE = re.compile(r"(?m)^[ \t]*(#{1,6})[ \t]*(.+?)[ \t]*$")
#: 标题里的序号（①、1.、一、）不算标题本身。
_BRIEF_TITLES = ("项目是什么", "项目简介", "项目概览")
_ORDINAL_RE = re.compile(r"^[\s①②③④⑤⑥⑦⑧⑨⑩0-9一二三四五六七八九十.、,)]+")

#: 话题文档模板里「现状」那一块（`doc_form.md` 与提示词同源）。
STATUS_TITLES = ("现状",)
#: 旧模板里对应的那一块。按旧模板写的文档在芝士下次更新之前没有「现状」，总览退回
#: 取它的第一句，不显示成「没写」。
FORMER_STATUS_TITLES = ("当前结论", "结论")
_SENTENCE_END_RE = re.compile(r"[。！？!?；;]|\.\s")


def render_overview(*, brief: str, auto: str) -> str:
    """注入用的整份总览：① 的正文，加上（只在总览房间里）②③。

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


def topic_status(content: str) -> str | None:
    """一份话题文档的「现状」第一句，没有就是 None。

    注入到总览 ② / ③ 的那一句。取不到就报 None——编一个「进行中」出来，比
    留空更容易被当成事实。没有「现状」时退回旧模板的「当前结论」。
    """
    sections = _sections(content)
    for names in (STATUS_TITLES, FORMER_STATUS_TITLES):
        for _level, title, body in sections:
            if _is_titled(title, names):
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


#: 自动区各块的键与标题。键是给机器认的（前端按它选渲染方式，测试按它找块），
#: 标题是给人读的；两者在 markdown 和结构化数据里必须是同一份，不然同一块会有
#: 两个名字。
ACTIVE_TOPICS_KEY, ACTIVE_TOPICS_TITLE = "active_topics", "现在在做什么"
CLOSED_TOPICS_KEY, CLOSED_TOPICS_TITLE = "closed_topics", "已结束的话题"


# ---- ②③：条目只定义一次，两个读者各自排版 ----
#
# 这几块有两个读者：提示词要一段 markdown，总览那一栏要能逐个点击的结构化数据
# （`GET /topics/{id}/overview`）。所以字段在 `_*_item()` 里定义一次，两个公开
# 函数只负责排版——各拼一遍、各定义一套字段，才是两边迟早对不上的写法。


def _topic_item(row: dict) -> dict:
    """话题在列表里的一行。`owner` / `status` 只有活跃的那几行才有。

    ``kind`` 说的是这一行的去处（一个话题房间），不是它在哪一块里——已结束的话题
    也是同一个去处，只是分组不同。
    """
    return {
        "kind": "topic",
        "topic_id": str(row["id"]),
        "title": row["title"],
        "owner": row.get("owner") or None,
        "status": row.get("status") or None,
        "conclusion": row.get("conclusion") or None,
    }


def _active_items(rows: list[dict]) -> list[dict]:
    return [_topic_item(t) for t in rows[:ACTIVE_TOPICS_LIMIT]]


def _closed_items(rows: list[dict]) -> list[dict]:
    return [_topic_item(t) for t in rows[:CLOSED_TOPICS_LIMIT]]


def _active_row(item: dict) -> str:
    return _row(
        [
            f"<#{item['topic_id']}> {item['title']}",
            f"负责人：{item['owner']}" if item["owner"] else None,
            item["status"],
            f"现状：{item['conclusion']}" if item["conclusion"] else "现状：（没写）",
        ]
    )


def _closed_row(item: dict) -> str:
    return _row(
        [
            f"<#{item['topic_id']}> {item['title']}",
            f"结论：{item['conclusion']}" if item["conclusion"] else "结论：（没写）",
        ]
    )


def render_overview_auto(
    *,
    active_topics: list[dict],
    closed_topics: list[dict],
) -> str:
    """②③ 拼成一段 markdown；两块都空时给空串。

    每块的输入是**已经取好的结构化行**（见模块顶部的字段约定），所以这里只排版、
    不查库。空块整块不出现：一份「## 已结束的话题（暂无）」对读者是噪音，对这个
    项目有没有结束过话题这件事毫无帮助。
    """
    blocks = [
        _block(
            f"## {ACTIVE_TOPICS_TITLE}",
            [_active_row(i) for i in _active_items(active_topics)],
        ),
        _block(
            f"## {CLOSED_TOPICS_TITLE}",
            [_closed_row(i) for i in _closed_items(closed_topics)],
        ),
    ]
    blocks = [b for b in blocks if b]
    if not blocks:
        return ""
    return (
        "以下两块由平台从结构化数据现拼（话题、结论卡），"
        "不在本文档正文里，也不要往正文里抄：\n\n" + "\n\n".join(blocks)
    )


def overview_auto_blocks(
    *,
    active_topics: list[dict],
    closed_topics: list[dict],
) -> list[dict]:
    """②③ 的结构化形态：``[{key, title, items}]``，**空块不出现**（同
    `render_overview_auto`——一份「已结束的话题（暂无）」对读者也是噪音）。

    和 markdown 那一份读的是同一个 `_*_items()`，所以两头永远不会各说各的。
    """
    built = [
        (ACTIVE_TOPICS_KEY, ACTIVE_TOPICS_TITLE, _active_items(active_topics)),
        (CLOSED_TOPICS_KEY, CLOSED_TOPICS_TITLE, _closed_items(closed_topics)),
    ]
    return [
        {"key": key, "title": title, "items": items}
        for key, title, items in built
        if items
    ]
