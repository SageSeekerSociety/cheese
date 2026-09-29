"""把每一份「已经交过 / 判过」的领取的完成状态补上（存量回填）。

``task_membership.completion_status`` 这条轴在 2026-09 之前没有推进者：建领取写一次
``NOT_SUBMITTED``，逾期清扫写一次 ``FAILED``，中间那三步（交了、判了、退回来了）谁也
没写。于是库里凡是交过作业的领取，不管评审在队列里还是已经判通过，都还是
``NOT_SUBMITTED`` —— 看板的完成率、题目板「我的」那一页的状态标签全跟着错。

现在写入侧已经有唯一的推进者（``app.domain.task.submission_state``：建/改提交与建/
改/删评审都在自己落库之后重推一次），但那只对**新发生的事**生效；**存量行**得靠这个
脚本跑一遍，否则新旧混杂，看板还是错的。

判据不在这里重写一份：脚本对每条领取调用同一个 ``refresh_completion_status``，也就是
请求路径上用的那个函数，读的也是同一组谓词（是否有 live 提交判通过 / 还在队列 / 全被
驳回，一版没交时看截止）。

**可重复执行**：值是从提交与评审推出来的，不是累加的，所以第二遍推出来的值与第一遍
写进去的相同，``refresh_completion_status`` 只在变了时才写 —— 第二遍是彻底的 no-op
（``--apply`` 一行都不写），这是它敢在开发库上跑两遍的前提。

默认 dry-run，一行不写：

    uv run python scripts/backfill_completion_status.py            # 只报数
    uv run python scripts/backfill_completion_status.py --apply    # 落库
"""

import asyncio
import sys
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_factory
from app.domain.task.models import TaskMembership
from app.domain.task.submission_state import (
    derive_completion_status,
    refresh_completion_status,
)

#: 一次取多少条领取。按 id 翻页（``id > 上一批最后一个``），不按 offset —— 一边写
#: 一边用 offset 会漏行。
BATCH_SIZE = 500


@dataclass
class BackfillReport:
    """跑了什么、变了什么。字段都是计数，好直接拿去和跑之前的快照对照。"""

    scanned: int = 0
    changed: int = 0
    before: Counter[str] = field(default_factory=Counter)
    after: Counter[str] = field(default_factory=Counter)
    transitions: Counter[str] = field(default_factory=Counter)

    def describe(self) -> str:
        lines = [
            f"scanned: {self.scanned} membership(s)",
            f"changed: {self.changed}",
            f"before: {dict(sorted(self.before.items()))}",
            f"after:  {dict(sorted(self.after.items()))}",
        ]
        if self.transitions:
            lines.append("transitions:")
            for transition, count in sorted(self.transitions.items()):
                lines.append(f"  {transition}: {count}")
        return "\n".join(lines)


async def backfill(
    session: AsyncSession,
    *,
    apply: bool,
    batch_size: int = BATCH_SIZE,
) -> BackfillReport:
    """扫一遍所有没被软删的领取，按提交与评审重推完成状态。

    ``apply=False`` 只推不写（dry run）；``apply=True`` 分批写并提交。
    """
    report = BackfillReport()
    last_id = 0

    while True:
        memberships = list(
            (
                await session.execute(
                    select(TaskMembership)
                    .where(
                        TaskMembership.deleted_at.is_(None),
                        TaskMembership.id > last_id,
                    )
                    .order_by(TaskMembership.id)
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )
        if not memberships:
            break

        for membership in memberships:
            before = membership.completion_status
            report.before[before] += 1
            report.scanned += 1
            if apply:
                after = await refresh_completion_status(session, membership)
            else:
                after = await derive_completion_status(session, membership)
            report.after[after] += 1
            if after != before:
                report.changed += 1
                report.transitions[f"{before} -> {after}"] += 1

        # 先记住翻页游标再提交：commit 会让这些对象过期，之后再读 id 要多跑一趟查询。
        last_id = int(memberships[-1].id)
        if apply:
            await session.commit()

    return report


async def _main(apply: bool) -> None:
    async with async_session_factory() as session:
        report = await backfill(session, apply=apply)
    print(report.describe())
    if not apply:
        print("\nDRY RUN — 一行都没写。加 --apply 落库。")


if __name__ == "__main__":
    asyncio.run(_main(apply="--apply" in sys.argv))
