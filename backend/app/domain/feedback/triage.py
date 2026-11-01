"""The batch of reports a scheduled triage run hands to an AI teammate.

A routine whose schedule carries `feedback_batch` (see `routine/schedule.py`)
gets, each time it fires, the oldest reports nobody has picked up appended to
its work, and the steps for triaging them. The selection is the platform's, not
the agent's, so two days' runs do not each decide differently what "untriaged"
means, and a day with nothing waiting runs no turn at all.

**Untriaged** is 已收录 with no assignee: assigning and claiming both move a
report to 处理中 (`FeedbackService.advance`), and a claim that is let go of puts
it back at 已收录 (`FeedbackService.release`) — releasing means 「as if it had
never been claimed」, so the report carries no trace of it.

**A report is handed out until somebody follows it up, not once.** Every
follow-up a run can leave is on the report itself: the teammate keeps the claim
while a task, a question to the rule's owner or a proposal awaits its outcome
(see `STEPS`), and a decision moves the status. So a report still untriaged
once the run that had it is over was given up on, and is offered again. What
the caller passes (`offered`, from the rule's runs) only keeps a report out of
a second batch while a run that has it is still going, and puts the reports
handed out longest ago behind the ones never handed out — so a report that
keeps being skipped does not crowd the newer ones out of every batch. That
record belongs to the rule, not to the report. Only reports the teammate could
read are handed out —
public and not flagged as a security matter, the arm `may_see` opens to a
non-admin. The private and security queues stay with the people on the triage
roster.

The agent does not change a status here. It claims what it works on, asks the
routine's owner whether to make a task of what it can fix — a task made on the
owner's word, whose commit carries the `Fixes-feedback:` line (the deploy then
moves the report) — and what should be declined or decided by someone else
goes into its run report as a proposal for the routine's owner; the status
itself stays a triage admin's call. A run changes no code: that is done in a
task.
"""

from __future__ import annotations

from sqlalchemy import Subquery, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.feedback.announce import KIND_LABELS
from app.domain.feedback.models import Feedback, FeedbackStatus, FeedbackVisibility

#: The most reports one run may be handed: a run is one turn of one teammate.
MAX_BATCH = 10

STEPS = "\n".join(
    (
        "逐条处理：",
        "1. 用 cheese_feedback(action=get) 读全文和评论。",
        "2. 用 cheese_feedback(action=claim) 领下来；领不到（已经有人在修，或者已经办完）"
        "就跳过这一条。",
        "3. 调查：复现、读代码、查日志，判断是不是真问题、根因在哪。",
        "4. 能修的先用 cheese_ask 问规则的主人要不要建任务修，一条反馈一道题，选项"
        "「建任务」「不修」；他选了「建任务」，再用 cheese_task(action=open) 另开一件任务（owner 填"
        "他），写清根因、修法和反馈编号。任务里修好的提交单独一行写 "
        "`Fixes-feedback: FB-<编号>`，合并上线后平台会自动记「已修复」「已上线」。"
        "这次不改代码。",
        "5. 修不了或不该修（设计如此、复现不了、要别人拍板）：不要改它的状态。"
        "把你的判断和建议（例如「建议标为不修复，因为……」）写进交回结果的 summary，"
        "由规则的主人决定。",
        "6. 有了后续（建了任务、问题在等回答、建议在等主人决定）的，领取留在你名下，"
        "别放掉：领取就是这条反馈有人跟进的记录。什么后续都没有的（没查完、查不下去），"
        "用 cheese_feedback(action=release) 放掉，它会回到队列，之后再交给队友。",
        "交回结果时逐条写清：编号、结论（创建的任务 / 建议 / 跳过）和理由。",
    )
)


async def untriaged(
    session: AsyncSession, limit: int, *, offered: Subquery | None = None
) -> list[Feedback]:
    """The public reports nobody has picked up, at most `limit`: never handed
    out first, then those handed out longest ago, oldest report first.

    `offered`, when given, has one row per report this rule handed out
    (`feedback_id`, `last_at`, `open` — whether a run that has it is still
    going). It comes from the caller because that record lives on the rule's
    runs, not on the report.
    """
    query = select(Feedback).where(
        Feedback.deleted_at.is_(None),
        Feedback.status == FeedbackStatus.received,
        Feedback.assignee_handle.is_(None),
        Feedback.visibility == FeedbackVisibility.public,
        Feedback.security.is_(False),
    )
    order = [Feedback.created_at, Feedback.display_no]
    if offered is not None:
        query = query.outerjoin(offered, offered.c.feedback_id == Feedback.id).where(
            offered.c.open.is_not(True)
        )
        order.insert(0, offered.c.last_at.asc().nulls_first())
    return list(await session.scalars(query.order_by(*order).limit(limit)))


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
