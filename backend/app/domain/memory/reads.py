"""记忆的**正文**到底被读过几次：按项目、按天。

换了文件式记忆之后，每轮注入的是 L1 索引（`MEMORY.md` 那份一行行的指针），正文要
agent 自己去读一个文件。整件事成立的前提就是**它会去读**：索引里的钩子只够判断
「这条相关」，剩下的话在正文里。

所以这个数是一个试点要回答的问题：一周下来，如果正文的读取接近 0，那这套机制就只是
把「一条条记忆」换成了「一条条指针」——写记忆的力气全花了，读的时候一条都没进去。
（回答若是「确实是 0」，下一步是让索引行本身更有信息量，或者把最常要用的几条正文也
放进注入预算；不是回去做关键词召回。）

## 数的是什么

一次**工具调用记录**，三个条件同时成立才算：

1. 工具是 `Read`（`meta.tool`，见 `agent/tool_preview.py` 与
   `turn.intake.events._persist_tool_event`）；
2. 参数里那个路径在 `.cheese/memory/` 下面（`meta.detail`，**未经剪裁的原文**——
   `meta.arg` 是给人看的预览，长路径会被剪成 `…/team/x.md`，拿它判目录是不准的）；
3. 那个文件不是 `MEMORY.md`：索引每轮注入，读它不算「翻正文」。

**这是工具调用，不是「记住了什么」。** agent 读了正文又没读懂，这里照样 +1：这个数
回答的是「有没有人翻开」，不是「有没有用上」。要回答后者得看别的东西。

数的是事件块（`blocks.kind = 'event'`，`meta` 是 `chat._tool_event_meta` 写的那份
JSON），所以**历史是免费的**：不用新表、不用新迁移，什么时候想数就什么时候查。代价
是这个查询要按时间窗扫一遍事件块（`ix_blocks_project_id` 领路，`meta` 那两列在
堆上比），所以它是给运营按天看趋势用的，不是高频接口。
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, and_, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import Block, BlockKind
from app.domain.platform_stats.windows import utc_day

#: 会话机上的记忆目录。`Read` 的参数是**绝对路径**（`$HOME/.cheese/memory/team/x.md`
#: 展开之后），所以判据是「含有这一段」而不是「以它开头」：不同机器的家目录不一样。
MEMORY_DIR_MARKER = ".cheese/memory/"

#: 索引文件名。它不算正文（见模块开头第 3 条）。
INDEX_NAME = "MEMORY.md"

#: 这一条事件说的是哪个工具。
TOOL_META_KEY = "tool"
#: 这个工具的参数原文（未剪裁）。
DETAIL_META_KEY = "detail"

READ_TOOL = "Read"


def is_body_read(tool: object, detail: object) -> bool:
    """这一条工具调用算不算读了一次记忆正文。

    判据的纯函数版本，和 :func:`body_reads` 里那两条 SQL 谓词逐字对应。两处都写
    是因为一边要能被人读、被单测钉，另一边要在数据库里跑；共享常量就是为了让它
    们不会各自漂开（改了 `MEMORY_DIR_MARKER`，两边一起改）。
    """
    if not isinstance(tool, str) or tool != READ_TOOL:
        return False
    if not isinstance(detail, str):
        return False
    normalized = detail.replace("\\", "/")
    marker = normalized.find(MEMORY_DIR_MARKER)
    if marker < 0:
        return False
    relative = normalized[marker + len(MEMORY_DIR_MARKER) :]
    return relative != INDEX_NAME and not relative.endswith(f"/{INDEX_NAME}")


def _tool_matches() -> ColumnElement[bool]:
    return Block.meta[TOOL_META_KEY].as_string() == READ_TOOL


def _path_matches() -> ColumnElement[bool]:
    # 反斜杠先换成正斜杠，和 `is_body_read` 那句 `replace` 是同一个动作：两份实现
    # 逐字对应，才不会在「同一批输入喂两边、两边答案不一样」的地方分叉。
    detail = func.replace(Block.meta[DETAIL_META_KEY].as_string(), "\\", "/")
    return and_(
        detail.like(f"%{MEMORY_DIR_MARKER}%"),
        # 索引那一条排除掉。用正则而不是 `LIKE`：锚在**末尾**才和 `is_body_read`
        # 是同一条判据——`…/MEMORY.md.bak` 不是索引，而 `LIKE '%MEMORY.md%'` 会
        # 把它一起挡掉，让这个数和纯函数那一版对不上。
        not_(detail.regexp_match(rf"{re.escape(INDEX_NAME)}$")),
    )


@dataclass(frozen=True)
class DayReads:
    day: str
    reads: int


@dataclass(frozen=True)
class ProjectReads:
    """一个项目在这个窗口里，每天读了多少次正文。"""

    project_id: str
    total: int
    days: list[DayReads]

    def as_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "total": self.total,
            "days": [{"day": day.day, "reads": day.reads} for day in self.days],
        }


async def body_reads(
    session: AsyncSession,
    *,
    since: datetime,
    until: datetime,
    project_id: uuid.UUID | None = None,
) -> list[ProjectReads]:
    """按 `(项目, 天)` 数正文读取；没有读过正文的项目不会出现。"""
    # 按 **UTC 的天**分桶：单参数的 `date_trunc('day', timestamptz)` 按会话时区切
    # 天，而这个库的会话时区不一定是 UTC（本机是 `Asia/Shanghai`）——那会把每天
    # 的边界挪几小时，还会让返回的那个日期字符串和实际分桶的那条边界差一天。看板
    # 已经有一份定义（`platform_stats.windows.utc_day`），这里用它，不另写一个。
    day = utc_day(Block.created_at)
    stmt = (
        select(Block.project_id, day.label("day"), func.count().label("reads"))
        .where(
            Block.kind == BlockKind.event,
            Block.created_at >= since,
            Block.created_at < until,
            _tool_matches(),
            _path_matches(),
        )
        .group_by(Block.project_id, day)
        .order_by(Block.project_id, day)
    )
    if project_id is not None:
        stmt = stmt.where(Block.project_id == project_id)
    rows = (await session.execute(stmt)).all()
    by_project: dict[str, list[DayReads]] = {}
    for project, bucket, reads in rows:
        by_project.setdefault(str(project), []).append(
            DayReads(day=bucket.date().isoformat(), reads=int(reads))
        )
    return [
        ProjectReads(
            project_id=project,
            total=sum(item.reads for item in days),
            days=days,
        )
        for project, days in sorted(by_project.items())
    ]
