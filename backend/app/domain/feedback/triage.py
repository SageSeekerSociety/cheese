"""The batch of reports a scheduled triage run hands to an AI teammate.

A routine whose schedule carries `feedback_batch` (see `routine/schedule.py`)
gets, each time it fires, the oldest reports nobody has picked up appended to
its work, and the steps for triaging them. The selection is the platform's, not
the agent's, so two days' runs do not each decide differently what "untriaged"
means, and a day with nothing waiting runs no turn at all.

**Untriaged** is 已收录 with no assignee: assigning and claiming both move a
report to 处理中 (`FeedbackService.advance`), so a report still at `received`
is one nobody has touched. Only reports the teammate could read are handed out
— public and not flagged as a security matter, the arm `may_see` opens to a
non-admin. The private and security queues stay with the people on the triage
roster.

The agent does not change a status here. It claims what it works on, fixes what
it can with a `Fixes-feedback:` line (the deploy then moves the report), and
what should be declined or decided by someone else goes into its run report as
a proposal for the routine's owner — the status itself stays a triage admin's
call.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.feedback.announce import KIND_LABELS
from app.domain.feedback.models import Feedback, FeedbackStatus, FeedbackVisibility

#: The most reports one run may be handed: a run is one turn of one teammate.
MAX_BATCH = 10

STEPS = "\n".join(
    (
        "逐条处理：",
        "1. 用 cheese_feedback_get 读全文和评论。",
        "2. 用 cheese_feedback_claim 领下来；领不到（已经有人在修）就跳过这一条。",
        "3. 调查：复现、读代码、查日志，判断是不是真问题、根因在哪。",
        "4. 能修就修：开 PR，提交信息里单独一行写 `Fixes-feedback: FB-<编号>`，"
        "合并上线后平台会自动记「已修复」「已上线」。",
        "5. 修不了或不该修（设计如此、复现不了、要别人拍板）：不要改它的状态。"
        "把你的判断和建议（例如「建议标为不修复，因为……」）写进交回结果的 summary，"
        "由规则的主人决定；不再跟进的用 cheese_feedback_release 放掉。",
        "交回结果时逐条写清：编号、结论（已修 PR / 建议 / 跳过）和理由。",
    )
)


async def untriaged(session: AsyncSession, limit: int) -> list[Feedback]:
    """The oldest public reports nobody has picked up, at most `limit`."""
    return list(
        await session.scalars(
            select(Feedback)
            .where(
                Feedback.deleted_at.is_(None),
                Feedback.status == FeedbackStatus.received,
                Feedback.assignee_handle.is_(None),
                Feedback.visibility == FeedbackVisibility.public,
                Feedback.security.is_(False),
            )
            .order_by(Feedback.created_at, Feedback.display_no)
            .limit(limit)
        )
    )


def batch_prompt(rows: list[Feedback]) -> str:
    lines = [f"【待分诊的反馈】下面 {len(rows)} 条还没有人处理，最早提交的在前："]
    for row in rows:
        kind = KIND_LABELS.get(str(row.kind), str(row.kind))
        lines.append(
            f"- FB-{row.display_no}「{row.title}」（{kind}，"
            f"{row.created_at:%Y-%m-%d} 提交）"
        )
    lines += ["", STEPS]
    return "\n".join(lines)
