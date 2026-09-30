"""一个人一道题只领一次。

A claim is live unless it was rejected (``approved == 1``) or withdrawn
(``deleted_at``), so a pending application counts: its workspace was opened
the moment it was made.
"""

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domain.task.models import TaskMembership
from app.domain.team.models import TeamUserRelation


async def members_claiming_through_another_team(
    session: AsyncSession, *, task_id: int, team_id: int
) -> list[int]:
    """Members of ``team_id`` who already hold a live claim on this task
    through a different team."""
    other_member = aliased(TeamUserRelation)
    stmt = select(TeamUserRelation.user_id).where(
        TeamUserRelation.team_id == team_id,
        TeamUserRelation.deleted_at.is_(None),
        exists().where(
            TaskMembership.task_id == task_id,
            TaskMembership.is_team.is_(True),
            TaskMembership.member_id != team_id,
            TaskMembership.approved != 1,
            TaskMembership.deleted_at.is_(None),
            other_member.team_id == TaskMembership.member_id,
            other_member.user_id == TeamUserRelation.user_id,
            other_member.deleted_at.is_(None),
        ),
    )
    return sorted(set((await session.execute(stmt)).scalars().all()))
