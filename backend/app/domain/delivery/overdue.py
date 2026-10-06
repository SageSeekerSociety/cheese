"""Tell a person when an agent's delivery has been owed for too long.

A delivery whose attempt is not admitted goes back to ``pending`` and is tried
again. No single attempt is an error, so nothing ever said so: on 2026-10-05 a
task's scheduled delivery was refused 1,209 times over 17 hours, and 31 others
looped beside it, until someone asked why the task was always queued. This
reports the deliveries still owed long after they were recorded, once an hour
(the alert's key makes every run the same problem) for as long as any remain.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select

from app.core import alerting
from app.domain.delivery.agent import now
from app.domain.delivery.models import Delivery

logger = logging.getLogger(__name__)

#: Past this, a delivery is not waiting for a room to come back; it is stuck.
OVERDUE_AFTER = timedelta(minutes=30)
#: States the dispatcher keeps trying. ``sending`` is one attempt in flight,
#: and ``uncertain`` and ``failed`` are never retried, so none of them loops.
OWED = ("pending", "claimed")
#: Conversations named in one alert; the rest are counted.
NAMED = 5


async def report_overdue(sessions) -> int:
    """Alert on agent deliveries still owed past ``OVERDUE_AFTER``; how many."""
    async with sessions() as session:
        rows = (
            await session.execute(
                select(
                    Delivery.conversation_id,
                    Delivery.recipient_handle,
                    func.count(),
                    func.max(Delivery.attempts),
                    func.min(Delivery.recorded_at),
                    func.max(Delivery.last_error),
                )
                .where(
                    Delivery.agent_instance_id.is_not(None),
                    Delivery.sent_at.is_(None),
                    Delivery.state.in_(OWED),
                    Delivery.recorded_at < now() - OVERDUE_AFTER,
                )
                .group_by(Delivery.conversation_id, Delivery.recipient_handle)
                .order_by(func.min(Delivery.recorded_at))
            )
        ).all()
    if not rows:
        return 0
    total = sum(count for _, _, count, _, _, _ in rows)
    logger.warning(
        "%d agent deliveries owed for over %s in %d conversations",
        total,
        OVERDUE_AFTER,
        len(rows),
    )
    lines = [
        f"共 {total} 条，涉及 {len(rows)} 个对话的座位，"
        f"都已超过 {int(OVERDUE_AFTER.total_seconds() // 60)} 分钟"
    ]
    for conversation, seat, count, attempts, oldest, error in rows[:NAMED]:
        lines.append(
            f"对话 {conversation} · {seat}：{count} 条，最多已试 {attempts} 次，"
            f"最早一条记于 {oldest:%m-%d %H:%M} UTC；最近的原因：{error or '无'}"
        )
    if len(rows) > NAMED:
        lines.append(f"另有 {len(rows) - NAMED} 个座位未列出")
    alerting.send("AI 队友的投递一直没送出去", lines, key="delivery:overdue")
    return total
