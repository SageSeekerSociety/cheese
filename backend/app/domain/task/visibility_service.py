from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domain.space.repositories import (
    SpaceAdminRelationRepository,
    SpaceRepository,
)
from app.domain.task.models import Task, TaskAccessDomain, TaskMembership
from app.domain.team.models import TeamUserRelation
from app.domain.user.repositories import UserRepository


class TaskVisibilityService:
    """谁看得见一道题（也就是谁领得了它）。

    题目只给它那块板里的人：出题者、本版管理员、已经领过它的人（个人领或经小队
    领）总看得见；其余的人先得是这块板的成员，题目开了可见范围（邮箱域名白名单）
    时还得在名单里。不在板里的人既读不到它，也在列表和搜索里找不到它。
    ``can_view_task`` 与 ``build_visibility_predicate`` 是同一条规则的两种形态。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._user_repo = UserRepository(session=session)
        self._admin_repo = SpaceAdminRelationRepository(session=session)
        self._space_repo = SpaceRepository(session=session)

    async def can_view_task(self, *, task: Task, user_id: int) -> bool:
        if user_id <= 0:
            return False
        if task.creator_id == user_id:
            return True
        if await self._is_space_admin(space_id=task.space_id, user_id=user_id):
            return True
        if await self.is_participant(task_id=task.id, user_id=user_id):
            return True
        if not await self._space_repo.is_member(
            space_id=task.space_id, user_id=user_id
        ):
            return False
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

        admitted: list[ColumnElement[bool]] = [Task.access_control_enabled.is_(False)]
        if email_domain:
            admitted.append(
                exists().where(
                    TaskAccessDomain.task_id == Task.id,
                    TaskAccessDomain.deleted_at.is_(None),
                    TaskAccessDomain.domain == email_domain,
                )
            )
        in_space = SpaceRepository.build_member_of_predicate(
            space_id=Task.space_id, user_id=user_id
        )

        return or_(
            Task.creator_id == user_id,
            participant_exists,
            and_(in_space, or_(*admitted)),
        )

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

    async def is_participant(self, *, task_id: int, user_id: int) -> bool:
        """这个人在这道题上有没有一份没删的申请（个人申请，或所在小队申请）——等批的、
        被拒的都算。「看得见这道题」问的是这一条：申请过的人总看得见自己申请的题。"""
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
        # One row is enough to answer, and there can be several: a person whose
        # team's claim was rejected may claim again through another team.
        result = await self._session.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None

    async def _is_domain_allowed(self, *, task_id: int, domain: str) -> bool:
        stmt = select(TaskAccessDomain.id).where(
            TaskAccessDomain.task_id == task_id,
            TaskAccessDomain.deleted_at.is_(None),
            TaskAccessDomain.domain == domain,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None


async def resolve_user_email_domain(session: AsyncSession, user_id: int) -> str | None:
    """这个读者的邮箱域名（小写），没有就 ``None`` —— 可见性判据要的那一格。"""
    user = await UserRepository(session=session).get_by_id(user_id)
    if user is None:
        return None
    if user.email_domain:
        return user.email_domain.lower()
    if user.email and "@" in user.email:
        return user.email.split("@", 1)[1].lower()
    return None
