from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domain.space.repositories import SpaceAdminRelationRepository
from app.domain.cx_task.models import Task, TaskAccessDomain, TaskMembership
from app.domain.team.models import TeamUserRelation
from app.domain.user.repositories import UserRepository


class TaskVisibilityService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._user_repo = UserRepository(session=session)
        self._admin_repo = SpaceAdminRelationRepository(session=session)

    async def can_view_task(self, *, task: Task, user_id: int) -> bool:
        if user_id <= 0:
            return not task.access_control_enabled
        if task.creator_id == user_id:
            return True
        if await self._is_space_admin(space_id=task.space_id, user_id=user_id):
            return True
        if await self._is_participant(task_id=task.id, user_id=user_id):
            return True
        if not task.access_control_enabled:
            return True

        email_domain = await self._get_user_email_domain(user_id)
        if not email_domain:
            return False
        return await self._is_domain_allowed(task_id=task.id, domain=email_domain)

    @staticmethod
    def build_visibility_predicate(
        *, user_id: int, email_domain: str | None
    ) -> ColumnElement[bool]:
        participant_exists = exists().where(
            TaskMembership.task_id == Task.id,
            TaskMembership.deleted_at.is_(None),
            or_(
                and_(
                    TaskMembership.is_team.is_(False),
                    TaskMembership.member_id == user_id,
                ),
                and_(
                    TaskMembership.is_team.is_(True),
                    exists().where(
                        TeamUserRelation.team_id == TaskMembership.member_id,
                        TeamUserRelation.user_id == user_id,
                        TeamUserRelation.deleted_at.is_(None),
                    ),
                ),
            ),
        )

        predicates = [
            Task.creator_id == user_id,
            Task.access_control_enabled.is_(False),
            participant_exists,
        ]

        if email_domain:
            predicates.append(
                exists().where(
                    TaskAccessDomain.task_id == Task.id,
                    TaskAccessDomain.deleted_at.is_(None),
                    TaskAccessDomain.domain == email_domain,
                )
            )

        return or_(*predicates)

    async def _get_user_email_domain(self, user_id: int) -> str | None:
        user = await self._user_repo.get_by_id(user_id)
        if user is None:
            return None
        if user.email_domain:
            return user.email_domain.lower()
        if user.email and "@" in user.email:
            return user.email.split("@", 1)[1].lower()
        return None

    async def _is_space_admin(self, *, space_id: int, user_id: int) -> bool:
        relation = await self._admin_repo.get_relation(space_id, user_id)
        return relation is not None

    async def _is_participant(self, *, task_id: int, user_id: int) -> bool:
        stmt = select(TaskMembership.id).where(
            TaskMembership.task_id == task_id,
            TaskMembership.deleted_at.is_(None),
            or_(
                and_(
                    TaskMembership.is_team.is_(False),
                    TaskMembership.member_id == user_id,
                ),
                and_(
                    TaskMembership.is_team.is_(True),
                    exists().where(
                        TeamUserRelation.team_id == TaskMembership.member_id,
                        TeamUserRelation.user_id == user_id,
                        TeamUserRelation.deleted_at.is_(None),
                    ),
                ),
            ),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def _is_domain_allowed(self, *, task_id: int, domain: str) -> bool:
        stmt = select(TaskAccessDomain.id).where(
            TaskAccessDomain.task_id == task_id,
            TaskAccessDomain.deleted_at.is_(None),
            TaskAccessDomain.domain == domain,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None
