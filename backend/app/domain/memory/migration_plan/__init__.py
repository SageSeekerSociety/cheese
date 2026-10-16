"""一份迁移计划：一条旧记忆去哪儿、要写哪些文件、报告上写着什么。

输入是两样——旧记忆（`migration_sources.OldEntry`）和模型给出的那串决定
（`decode_answer`）；输出是 `MigrationPlan`：要写的文件、要改的索引、只建议不自动
改的那些、以及那份给人复核的报告（`render_report`，`MigrationPlan.report()` 就是
它）。

**全是纯的**：没有数据库、没有 LLM。所以「每一条都有去处」「private 不许升级进
team」这两条可以在单测里直接问，不用先跑一次模型。

这一层判的是「这份计划搬不搬得动」：`build_plan` 之前，模型已经给每条旧记忆派了去
处，这里逐条校验（去处不合法、缺理由、目标文件不存在、private 升级进 team……），再
把同一个文件的几条决定合成一份内容。每一条拒绝都点名**是哪一条、哪里不对**——一次
迁移要人复核，读报告的人（和改 prompt 的人）要能从那句话里知道下一步动什么，而不是
只知道「失败了」。

这一份原来是 `migration_plan.py` 一个 727 行的模块，按职责拆成了包，名字和公开面
一个都没变：

- `plan.py`：计划的形状与常量——`Destination`、`Decision`、`PlannedFile`、
  `PlannedIndex`、`Suggestion`、`MigrationPlan`；
- `decode.py`：模型的回答 → 校验过的决定 → 一份计划——`decode_answer`、
  `build_plan`；
- `layout.py`：决定 → 要写的文件与索引；
- `report.py`：报告——`render_report`。

问模型的那段话在 `migration_prompt.py`；重导出这些名字的入口在 `migration.py`。
"""

from .decode import build_plan, decode_answer
from .plan import (
    REASON_MAX,
    REPORT_TITLE,
    TEAM_INDEX_GOAL_LINES,
    Decision,
    Destination,
    MigrationError,
    MigrationPlan,
    PlannedFile,
    PlannedIndex,
    Suggestion,
)
from .report import render_report

__all__ = [
    "REASON_MAX",
    "REPORT_TITLE",
    "TEAM_INDEX_GOAL_LINES",
    "Decision",
    "Destination",
    "MigrationError",
    "MigrationPlan",
    "PlannedFile",
    "PlannedIndex",
    "Suggestion",
    "build_plan",
    "decode_answer",
    "render_report",
]
