"""迁移的来源：旧表和项目目标文档，读成同一种东西。

两个来源读进来都是 `OldEntry`：旧表 `memory_entries` 里的行（由 `migration_service`
那一侧读出来），和项目目标文档里那两节（`OVERVIEW_SECTIONS`）。它们在报告里一个待
遇——一条一条列出来，各自有一个去处。

这一层只回答「旧的一边长什么样」：`Source` 说的是它从哪儿来（它决定了几条硬规矩，
比如「关于某个人的」不许进 team），`sources_digest` 是这一份旧记忆的指纹（`apply`
之前对一次，判报告还是不是当下的那一份）。去处合不合法、新树写成什么样，在
`migration_plan/`。
"""

import hashlib
from dataclasses import dataclass
from enum import StrEnum

#: 项目目标文档里那两节。它们是第三个来源，和人 / agent 的池子同一个待遇：一条一条
#: 列出来，各自有一个去处。**含标题不含正文**——「由记忆整理迁入」那一节是上一版
#: 迁移写进去的，而它写的正是旧表里的行，所以这一节和 `memory_entries` 有重叠；
#: 重叠由人（或模型）在报告里看见，不在这里去重：两处可能已经分叉了，按字符串去重
#: 只会把分叉的那一版静默丢掉。
OVERVIEW_SECTIONS = ("大家都该知道的", "项目记忆（由记忆整理迁入）")

#: 报告里一条旧记忆最多摘多少字。报告是给人扫的，不是把整张表倒出来。
EXCERPT_MAX = 160


class Source(StrEnum):
    """一条旧记忆从哪儿来。它决定了几条硬规矩。"""

    #: `scope=agent_project`：某个 agent 在这个项目里学到的。
    agent = "agent"
    #: `scope=user`：某个 agent 对某个人的认识。**不许进 team。**
    person = "person"
    #: 项目目标文档里的那一节。
    overview = "overview"


@dataclass(frozen=True)
class OldEntry:
    """一条旧记忆：一个稳定的名字、一段正文、和它从哪儿来。"""

    #: 报告和决定都靠它对齐。写进报告里给人看，所以它得是人读得懂的，不是 uuid
    #: ——报告上要人复核「这条去哪儿」，而 `3f2a…` 说不出是哪一条。
    source_id: str
    origin: Source
    #: 它原来在哪儿（人话）。报告里跟 source_id 一起出现。
    where: str
    content: str

    def excerpt(self, limit: int = EXCERPT_MAX) -> str:
        """报告里那一行摘要：折成一行，超长截断。"""
        text = " ".join(self.content.split())
        return text if len(text) <= limit else text[: limit - 1] + "…"


def overview_entries(doc: str) -> list[OldEntry]:
    """项目目标文档里那两节，读成一条条旧记忆。

    一节里的正文按**空行**切段：文档是给人看的，一段一件事，而段落是它唯一自带
    的结构。没有那两节就是空的——一份还没按这个结构写过的文档不该被硬读出东西来。
    """
    entries: list[OldEntry] = []
    for heading, body in _sections(doc):
        if heading not in OVERVIEW_SECTIONS:
            continue
        where = f"项目目标文档 · {heading}"
        for number, paragraph in enumerate(_paragraphs(body), start=1):
            entries.append(
                OldEntry(
                    source_id=f"overview:{heading}#{number}",
                    origin=Source.overview,
                    where=where,
                    content=paragraph,
                )
            )
    return entries


def _sections(doc: str) -> list[tuple[str, str]]:
    """`(标题, 正文)`，按出现顺序。只认 `##`（文档是 markdown，一节一级标题）。"""
    sections: list[tuple[str, str]] = []
    heading = ""
    lines: list[str] = []
    for line in doc.splitlines():
        if line.startswith("## "):
            if heading:
                sections.append((heading, "\n".join(lines)))
            heading = line[3:].strip()
            lines = []
            continue
        if heading:
            lines.append(line)
    if heading:
        sections.append((heading, "\n".join(lines)))
    return sections


def _paragraphs(body: str) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        if line.strip():
            current.append(line.rstrip())
            continue
        if current:
            paragraphs.append("\n".join(current).strip())
            current = []
    if current:
        paragraphs.append("\n".join(current).strip())
    return paragraphs


def sources_digest(entries: list[OldEntry]) -> str:
    """旧记忆这一份的指纹。`apply` 之前对一次，用来判「报告还是不是当下的那份」。"""
    material = "\n".join(
        f"{entry.source_id}\x00{entry.content}"
        for entry in sorted(entries, key=lambda item: item.source_id)
    )
    return hashlib.sha256(material.encode()).hexdigest()[:16]
