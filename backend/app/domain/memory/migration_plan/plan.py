"""一份计划的形状：一条旧记忆的去处、要写的文件、要改的索引，和只建议的那些。

全是数据，没有一步校验、没有一次渲染：`Destination` 说去处有哪五种（没有第六种
「先放着」），`Decision` 是一条旧记忆被模型派去了哪儿，`PlannedFile` /
`PlannedIndex` 是这一版要写下去的整份内容，`Suggestion` 是只建议、一个字都不自动
改的那些。`MigrationPlan` 把它们收在一起，并回答报告要用的那几个数。

把模型的回答读成这些形状、逐条校验，在 `decode.py`；把决定码成一份份文件在
`layout.py`；报告在 `report.py`。这一份只放它们共用的形状和常量。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.memory.files import (
    INDEX_LINE_MAX,
    MemoryFileScope,
    MemoryType,
    prefix_of,
)
from app.domain.memory.migration_sources import OldEntry

#: 迁移后 team 的 L1 索引目标行数。**是目标，不是闸**：超了照样出报告，但报告上
#: 要红着写出来——这次迁移的意义之一就是把索引压回去，压不回去得让人看见。
#: 上限本身是 200 行（`files.INDEX_MAX_LINES`），那是「读不读得到」的线。
TEAM_INDEX_GOAL_LINES = 120

#: 一条理由最多多少字。
REASON_MAX = 200

#: 一次迁移里，报告本身要留在哪。给人看的落点是项目总览房间，这份报告是附件。
REPORT_TITLE = "旧记忆迁移报告"


class Destination(StrEnum):
    """一条旧记忆的五种去处。**每一条都要有一个**，没有第六种「先放着」。

    ``merge`` 和 ``team`` / ``private`` 分开，是因为它们写的动作不一样：前者是
    往一条已经存在的记忆上补，后者是新开一条。报告要让人看出「这一版新开了 40
    个文件」和「这一版把 40 条并进了 12 个已有的」是两件事。
    """

    team = "team"
    private = "private"
    merge = "merge"
    #: 只建议写进 `CLAUDE.md` / `SKILL.md`，**一个字都不自动改**：那不是记忆的地
    #: 方，改它要另一次人的决定。
    suggest = "suggest"
    discard = "discard"


#: 每个去处允许的 `type`。`user` 永远 private（「这个人是谁」不是项目共识），
#: team 里也不该出现它——那正是「private 不许升级进 team」在类型上的样子。`merge`
#: 不在这张表里：并进去的那一段跟着目标文件自己的 type，不从这一条上取。
_ALLOWED_TYPES: dict[Destination, tuple[MemoryType, ...]] = {
    Destination.team: (
        MemoryType.project,
        MemoryType.reference,
        MemoryType.feedback,
    ),
    Destination.private: (MemoryType.user, MemoryType.feedback),
}


class MigrationError(ValueError):
    """这份计划搬不动：模型给的东西不合法，或者缺了谁的去处。

    理由里带上**是哪一条**、**哪里不对**：一次迁移要人复核，读报告的人（和改
    prompt 的人）要能从这句话里知道下一步动什么，而不是只知道「失败了」。
    """


@dataclass(frozen=True)
class Decision:
    """一条旧记忆的去处。"""

    source_id: str
    destination: Destination
    reason: str
    #: 新文件的 slug（`team/<path>.md` / `private/<owner>/<path>.md`），或 `merge`
    #: 的目标文件名。空的时候是它自己的 slug 都没给——`_check` 会拒。
    path: str = ""
    #: `private` / `merge` 的主人。
    owner: str = ""
    #: `merge` 的目标作用域：空串 = team。
    scope: MemoryFileScope | None = None
    type: MemoryType | None = None
    description: str = ""
    body: str = ""
    #: `suggest` 的落点：`CLAUDE.md` / `SKILL.md` / 别的文件名。
    target: str = ""

    def target_file(self) -> tuple[MemoryFileScope, str, str]:
        """这条决定要写进哪个文件：`(作用域, 主人, 文件名)`。"""
        owner = self.owner if self.scope is MemoryFileScope.private else None
        return (self.scope or MemoryFileScope.team), (owner or ""), f"{self.path}.md"

    def target_prefix(self) -> str:
        """这条决定动的是哪棵树：`team` 或 `private/<handle>`。"""
        return prefix_of(self.target_file()[0], self.target_file()[1])

    def target_path(self) -> str:
        """这条决定动的是哪个文件（带作用域前缀、带 `.md`）。`suggest` 没有目标
        文件——它说的是一份文档，那不是一个记忆文件，所以这里给空串。"""
        if self.destination is Destination.suggest:
            return ""
        scope, owner, name = self.target_file()
        return f"{prefix_of(scope, owner)}/{name}"

    def index_line(self) -> str:
        """这一条在索引里的那一行。

        `description` 是空的时候退回正文的头一句：索引里一行钩子都不给，等于这个
        文件在索引里没有名字。`description` 本身在校验时就要过，这里只是为了这
        个方法自己站得住。
        """
        hook = " ".join(self.description.split()) or " ".join(self.body.split())
        head = f"- [{self.path}]({self.path}.md) — "
        return head + hook[: max(INDEX_LINE_MAX - len(head), 0)]


@dataclass(frozen=True)
class PlannedFile:
    """要写下去的一个文件：整份新内容，和读到的那一版。"""

    scope: MemoryFileScope
    owner: str
    path: str
    content: str
    #: dry-run 时读到的那一版；新建是 None。apply 带上它，对不上就是有人在这期间
    #: 改过——那一条冲突会让整次搬迁停下来（见 `migration_service.apply`）。
    version: int | None
    #: 这份文件装着哪几条旧记忆。
    sources: tuple[str, ...] = ()
    #: 新建（True）还是并进已有的（False）。
    is_new: bool = True

    @property
    def prefixed(self) -> str:
        """报告和 diff 里用的完整路径（`team/x.md`）。"""
        return f"{prefix_of(self.scope, self.owner)}/{self.path}"


@dataclass(frozen=True)
class PlannedIndex:
    """一个作用域的 `MEMORY.md` 新内容。索引和正文分开写，因为它们的冲突面不一样。"""

    scope: MemoryFileScope
    owner: str
    content: str
    version: int | None
    #: 这一次新加的行数（报告用来说明索引是怎么涨的）。
    added_lines: int = 0


@dataclass(frozen=True)
class Suggestion:
    """一条只建议、不自动改的东西。"""

    source_id: str
    target: str
    text: str
    reason: str


@dataclass(frozen=True)
class MigrationPlan:
    """一次搬迁的全部内容：报告、要写的文件、和它算出来的账。"""

    project_id: str
    sources: tuple[OldEntry, ...]
    decisions: tuple[Decision, ...]
    files: tuple[PlannedFile, ...]
    indexes: tuple[PlannedIndex, ...]
    suggestions: tuple[Suggestion, ...]
    #: 旧表那几条的指纹。apply 前对一次：`dry-run` 之后旧表又被人写过，说明这份
    #: 报告描述的已经不是现在的旧表了，得重跑。
    sources_digest: str = ""
    #: 每条旧记忆的原样（模型的输入）。重建计划要用它，报告不用。
    raw_decisions: tuple[dict, ...] = field(default=())

    def team_index_lines(self) -> int:
        """迁移后 team 索引有多少行。"""
        for index in self.indexes:
            if index.scope is MemoryFileScope.team:
                return _line_count(index.content)
        return 0

    def over_goal(self) -> bool:
        return self.team_index_lines() > TEAM_INDEX_GOAL_LINES

    def counts(self) -> dict[str, int]:
        """每个去处各几条。报告开头那一行。"""
        tally = {destination.value: 0 for destination in Destination}
        for decision in self.decisions:
            tally[decision.destination.value] += 1
        return tally

    def report(self) -> str:
        # 报告的排版在 `report.py`，而它读的是这一份的形状，两边直接互相 import
        # 会成环。取的是包的入口——子模块引自己的包，边界契约不算环。
        # deferred-import: plan <-> report 会成环
        from app.domain.memory.migration_plan import render_report

        return render_report(self)


def _line_count(text: str) -> int:
    return len([line for line in text.splitlines() if line.strip()])
