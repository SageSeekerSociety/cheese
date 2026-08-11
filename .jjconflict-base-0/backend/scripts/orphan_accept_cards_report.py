"""孤儿卡核对 (2026-08-10) —— 只读，跑多少次都不会改任何东西。

"孤儿卡" = 停在**已归档话题**上、状态却还是非终态（pending / pending_gate /
conflict / pr_open）的验收卡。`pr_open` 那几张最要命：修复之前
`SchedulerService.poll_open_prs` 只按卡的 status 选行，所以它们每 60 秒还在被
推进——用的是 `decided_by` 那个人的 GitHub token。

用法（在能连到目标库的地方）::

    uv run python -m scripts.orphan_accept_cards_report

迁移 `b8e1d4c70a92` 会自动把这些卡收敛掉。这个脚本是用来**在迁移前后各跑一遍**
留证据的：前一次应该列出孤儿卡，后一次应该是 "没有孤儿卡"。
"""

import asyncio

from sqlalchemy import select

from app.core.db import async_session_factory
from app.domain.review.archive import OPEN_CARD_STATUSES
from app.domain.review.models import AcceptCard
from app.domain.topic.models import Topic, TopicStatus


async def report() -> list[dict]:
    async with async_session_factory() as s:
        rows = (
            await s.execute(
                select(AcceptCard, Topic)
                .join(Topic, Topic.id == AcceptCard.topic_id)
                .where(
                    Topic.status == TopicStatus.archived,
                    AcceptCard.status.in_(OPEN_CARD_STATUSES),
                )
                .order_by(Topic.archived_at)
            )
        ).all()

    found = []
    for card, topic in rows:
        stage = (
            "-"
            if card.status.value != "pr_open"
            else ("第二阶段/已合并等部署" if card.pr_merged_at else "第一阶段/PR未合并")
        )
        found.append(
            {
                "card_id": str(card.id),
                "status": card.status.value,
                "stage": stage,
                "topic": topic.title,
                "topic_id": str(topic.id),
                "archived_at": str(topic.archived_at),
                "decided_by": card.decided_by,
                "pr": f"#{card.pr_number}" if card.pr_number else "-",
                "pr_url": card.pr_url or "-",
            }
        )

    if not found:
        print("✅ 没有孤儿卡：已归档话题上没有任何非终态验收卡。")
        return found

    print(f"⚠️ 发现 {len(found)} 张孤儿卡（已归档话题 × 非终态验收卡）：\n")
    by_status: dict[str, int] = {}
    for f in found:
        by_status[f["status"]] = by_status.get(f["status"], 0) + 1
        print(
            f"  [{f['status']:<12}] {f['stage']:<18} PR {f['pr']:<6} "
            f"decided_by={f['decided_by'] or '-':<14} "
            f"归档于 {f['archived_at']}  「{f['topic']}」"
        )
        if f["pr_url"] != "-":
            print(f"       {f['pr_url']}")
    print(f"\n  小计：{by_status}")
    print(
        "\n  其中 pr_open 的那些，在修复前每 60 秒仍会被 poll_open_prs 推进"
        "（用 decided_by 的 GitHub token）。"
    )
    return found


if __name__ == "__main__":
    asyncio.run(report())
