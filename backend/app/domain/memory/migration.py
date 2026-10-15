"""旧记忆搬进文件树：先出一份报告，人点头了才写。

记忆换过一次形态（条目池 → 文件树，见 `files.py`），旧表 `memory_entries` 留着没
删。库里那些行还在被界面读，但它们**不进上下文**：agent 每天的索引里一个字都不是
它们的。所以这件事迟早要做一次，而且只做一次——一次性的迁移，不是一条常驻的路。

三件事让这件事和「写个脚本 UPDATE 一下」差得很远：

1. **去处不是一个表，是一棵有主人的树。** 旧的一条进哪儿，取决于它是「关于某个人
   的」还是「关于这个项目的」——同一个池子里两种都有。猜错了的代价不对称：把私人
   的话写进 team，是替整个项目定了规矩，而且再也收不回来（team 是所有人共读的）。
2. **搬迁要一次看全。** 一条一条搬的脚本只能做「原样挪过去」，而旧表里同一件事往
   往躺着五六个说法不同的版本。搬进文件树时把它们合成一条，才是这次搬家真正省的
   东西——不然新树一开始就背着旧表的重复。
3. **它动的是所有人和所有芝士共读的那份记忆。** 所以规矩是先报告、后写入，报告里
   每一条旧记忆都要说出自己的去处和一句理由，人看过、点过头（`approved_by`），
   `apply` 才落笔。

代码按「哪一步在判什么」分在三处，这一份把它们摆在原来的名字下面：

- `migration_sources.py`：旧的一边长什么样——`Source`、`OldEntry`、
  `overview_entries`、`sources_digest`。
- `migration_plan.py`：一条旧记忆的去处合不合法、新树写成什么样、报告怎么写——
  `Destination`、`Decision`、`MigrationPlan`、`build_plan`、`render_report` 等。
- `migration_prompt.py`：问模型的那段话——`MIGRATION_SYSTEM`、`migration_prompt`。

这三处都是纯的：没有数据库、没有 LLM，只有「旧记忆长什么样」「决定合不合法」「新
树会写成什么样」。所以「每一条都有去处」「private 不许升级进 team」这两条可以在
单测里直接问，不用先跑一次模型。

**旧表不删。** 这次只搬，删表是另一个迁移，要等搬完看一阵（30 天）再说。
"""

from app.domain.memory.migration_plan import (
    REPORT_TITLE,
    TEAM_INDEX_GOAL_LINES,
    Decision,
    Destination,
    MigrationError,
    MigrationPlan,
    PlannedFile,
    PlannedIndex,
    Suggestion,
    build_plan,
    decode_answer,
    render_report,
)
from app.domain.memory.migration_prompt import (
    MIGRATION_CHUNK,
    MIGRATION_SYSTEM,
    migration_prompt,
)
from app.domain.memory.migration_sources import (
    OVERVIEW_SECTIONS,
    OldEntry,
    Source,
    overview_entries,
    sources_digest,
)

__all__ = [
    "Destination",
    "Decision",
    "MigrationError",
    "MigrationPlan",
    "OldEntry",
    "OVERVIEW_SECTIONS",
    "PlannedFile",
    "PlannedIndex",
    "REPORT_TITLE",
    "Source",
    "Suggestion",
    "TEAM_INDEX_GOAL_LINES",
    "MIGRATION_CHUNK",
    "MIGRATION_SYSTEM",
    "build_plan",
    "decode_answer",
    "migration_prompt",
    "overview_entries",
    "render_report",
    "sources_digest",
]
