"""What waits on the people who teach a 题目: claims to approve and
submissions to review.

The people are the ones who may teach it (`app.auth.task_roles.teaches`):
its publisher and the admins of its space. Counted from the rows as they are
now, so a claim decided or a submission reviewed drops out by itself; a 题目
that ended or was deleted waits on nobody.

A submission waits for review when it is the claim's latest version, has no
review, and the claim's state is not already settled (passed or failed).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.space.models import Space, SpaceAdminRelation
from app.domain.task.models import (
    Task,
    TaskMembership,
    TaskSubmission,
    TaskSubmissionReview,
)

#: A claim's state once it is settled either way.
_SETTLED = ("SUCCESS", "FAILED", "REJECTED_RESUBMITTABLE")
#: Pending claims are ``approved == 2`` (``ApproveType.NONE``).
_UNDECIDED = 2


@dataclass(frozen=True, slots=True)
class Waiting:
    task_id: int
    task_name: str
    space_id: int
    space_name: str
    claims: int
    reviews: int
    at: datetime


async def teaching_queue(session: AsyncSession, user_id: int) -> Sequence[Waiting]:
    """The 题目 ``user_id`` teaches that have claims or submissions waiting,
    the latest first."""
    administered = select(SpaceAdminRelation.space_id).where(
        SpaceAdminRelation.user_id == user_id,
        SpaceAdminRelation.deleted_at.is_(None),
    )
    taught = and_(
        Task.deleted_at.is_(None),
        Task.ended_at.is_(None),
        or_(Task.creator_id == user_id, Task.space_id.in_(administered)),
    )

    claims = (
        select(
            TaskMembership.task_id,
            func.count().label("n"),
            func.max(TaskMembership.updated_at).label("at"),
        )
        .join(Task, Task.id == TaskMembership.task_id)
        .where(
            taught,
            TaskMembership.deleted_at.is_(None),
            TaskMembership.approved == _UNDECIDED,
        )
        .group_by(TaskMembership.task_id)
    )

    # Each stage is graded on its own, so "the latest version" is per stage:
    # a later stage reviewed first leaves the earlier one still waiting.
    latest = (
        select(
            TaskSubmission.membership_id,
            TaskSubmission.stage,
            func.max(TaskSubmission.version).label("version"),
        )
        .where(TaskSubmission.deleted_at.is_(None))
        .group_by(TaskSubmission.membership_id, TaskSubmission.stage)
        .subquery()
    )
    reviewed = (
        select(TaskSubmissionReview.id)
        .where(
            TaskSubmissionReview.submission_id == TaskSubmission.id,
            TaskSubmissionReview.deleted_at.is_(None),
        )
        .exists()
    )
    reviews = (
        select(
            TaskMembership.task_id,
            func.count().label("n"),
            func.max(TaskSubmission.created_at).label("at"),
        )
        .join(Task, Task.id == TaskMembership.task_id)
        .join(TaskSubmission, TaskSubmission.membership_id == TaskMembership.id)
        .join(
            latest,
            and_(
                latest.c.membership_id == TaskSubmission.membership_id,
                latest.c.stage == TaskSubmission.stage,
                latest.c.version == TaskSubmission.version,
            ),
        )
        .where(
            taught,
            TaskMembership.deleted_at.is_(None),
            TaskMembership.approved == 0,
            TaskSubmission.deleted_at.is_(None),
            func.upper(func.coalesce(TaskMembership.completion_status, "")).not_in(
                _SETTLED
            ),
            ~reviewed,
        )
        .group_by(TaskMembership.task_id)
    )

    waiting: dict[int, dict] = {}
    for task_id, n, at in (await session.execute(claims)).all():
        waiting.setdefault(int(task_id), {"claims": 0, "reviews": 0, "at": at})
        waiting[int(task_id)]["claims"] = int(n)
    for task_id, n, at in (await session.execute(reviews)).all():
        entry = waiting.setdefault(int(task_id), {"claims": 0, "reviews": 0, "at": at})
        entry["reviews"] = int(n)
        if at and (entry["at"] is None or at > entry["at"]):
            entry["at"] = at
    if not waiting:
        return []

    rows = (
        await session.execute(
            select(Task.id, Task.name, Task.space_id, Space.name)
            .join(Space, Space.id == Task.space_id)
            .where(Task.id.in_(waiting))
        )
    ).all()
    out = [
        Waiting(
            task_id=int(task_id),
            task_name=name,
            space_id=int(space_id),
            space_name=space_name,
            claims=waiting[int(task_id)]["claims"],
            reviews=waiting[int(task_id)]["reviews"],
            at=waiting[int(task_id)]["at"],
        )
        for task_id, name, space_id, space_name in rows
    ]
    out.sort(key=lambda w: w.at, reverse=True)
    return out
