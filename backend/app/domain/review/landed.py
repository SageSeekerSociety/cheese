"""Which task a commit on the project's default branch came from.

Only the platform's own record answers this: when an accept merges a task's
pull request, the merge commit it lands is written on the card
(`AcceptCard.pr_head_sha` once `pr_merged_at` is set). A commit's message is
not asked — its text is the repository's, anyone who can push can write a
line naming any task, and a commit pushed straight to the branch names none.
A commit no card landed came from no task the platform knows of.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.review.models import AcceptCard
from app.domain.room_task.models import Task


@dataclass(frozen=True)
class LandedTask:
    id: uuid.UUID
    title: str
    room_id: uuid.UUID


async def tasks_that_landed(
    session: AsyncSession, project_id: uuid.UUID, shas: list[str]
) -> dict[str, LandedTask]:
    """The task each of these commits landed, for the ones an accept in this
    project merged; a commit missing from the answer came from no task."""
    if not shas:
        return {}
    rows = await session.execute(
        select(AcceptCard.pr_head_sha, Task.id, Task.title, Task.room_id)
        .join(Task, Task.id == AcceptCard.task_id)
        .where(
            AcceptCard.pr_merged_at.is_not(None),
            AcceptCard.pr_head_sha.in_(shas),
            Task.project_id == project_id,
        )
    )
    return {
        sha: LandedTask(id=task_id, title=title, room_id=room_id)
        for sha, task_id, title, room_id in rows
    }
