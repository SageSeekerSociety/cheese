from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.team import permissions as team_seats
from app.domain.team.models import RecruitmentStatus, TeamRecruitmentPost
from app.domain.team.permissions import Permission as TeamPermission
from app.domain.team.recruitment_repositories import RecruitmentRepository
from app.domain.team.repositories import TeamRepository


class RecruitmentService:
    def __init__(
        self,
        repo: RecruitmentRepository,
        team_repo: TeamRepository,
    ) -> None:
        self._repo = repo
        self._team_repo = team_repo

    @classmethod
    def from_session(cls, session: AsyncSession) -> "RecruitmentService":
        """拿 session 直接造一个 —— 给**别的领域**用。

        要招人能力的调用方（HTTP 路由）不该知道底下的 repository 叫什么、怎么造：
        那一步是跨领域摸 repository，守卫拦的就是它。
        """
        return cls(
            repo=RecruitmentRepository(session=session),
            team_repo=TeamRepository(session=session),
        )

    async def create_post(
        self,
        *,
        team_id: int,
        actor_user_id: int,
        title: str,
        content: str,
        contact: str | None = None,
        max_members: int | None = None,
        expires_at: datetime | None = None,
    ) -> TeamRecruitmentPost:
        """Only ADMIN+ of the team can create a recruitment post."""
        team = await self._team_repo.get_by_id(team_id)
        if team is None:
            raise NotFoundError("Team not found", data={"type": "team", "id": team_id})
        if not await team_seats.may(
            self._team_repo._session, team_id, actor_user_id, TeamPermission.recruit
        ):
            raise ForbiddenError(
                "Only team admins or owner can create recruitment posts"
            )
        # Asked after the permission, not before: a personal team answers a
        # non-member with the same 403 a shared one does, so the refusal tells
        # nobody what kind of team they guessed the id of. To its owner: an ad
        # asking people to join a team that takes nobody can only disappoint
        # them, and a group that takes people is a shared team.
        if team.personal_owner_user_id is not None:
            raise BadRequestError("A personal team takes no recruits")
        return await self._repo.create(
            team_id=team_id,
            title=title,
            content=content,
            contact=contact,
            max_members=max_members,
            created_by=actor_user_id,
            expires_at=expires_at,
        )

    async def list_open(
        self,
        *,
        page_size: int = 20,
        page_start: int | None = None,
        keyword: str | None = None,
    ) -> tuple[list[TeamRecruitmentPost], bool, int | None]:
        """Public listing of all OPEN recruitment posts."""
        return await self._repo.list_open(
            page_size=page_size, page_start=page_start, keyword=keyword
        )

    async def list_by_team(self, team_id: int) -> list[TeamRecruitmentPost]:
        """List all recruitment posts for a specific team (any status)."""
        return await self._repo.list_by_team(team_id)

    async def get_by_id(self, post_id: int) -> TeamRecruitmentPost | None:
        return await self._repo.get_by_id(post_id)

    async def update_post(
        self,
        *,
        post_id: int,
        actor_user_id: int,
        title: str | None = None,
        content: str | None = None,
        contact: str | None = ...,  # type: ignore[assignment]
        max_members: int | None = ...,  # type: ignore[assignment]
        status: str | None = None,
        expires_at: datetime | None = ...,  # type: ignore[assignment]
    ) -> TeamRecruitmentPost:
        """Only the post creator can edit."""
        post = await self._repo.get_by_id(post_id)
        if post is None:
            raise NotFoundError(
                "Recruitment post not found",
                data={"type": "recruitment_post", "id": post_id},
            )
        if post.created_by != actor_user_id:
            raise ForbiddenError("Only the post creator can edit this recruitment post")
        # Editing the text or closing it is fine. Opening it again is not: the
        # creator of a post on a personal team is that team's owner, so this is
        # the route that could undo the withdrawal the migration made, and the
        # database refuses the same write (trigger
        # `team_recruitment_post_respects_personal_teams`).
        if status == RecruitmentStatus.OPEN.value:
            team = await self._team_repo.get_by_id(post.team_id)
            if team is not None and team.personal_owner_user_id is not None:
                raise BadRequestError("A personal team takes no recruits")
        return await self._repo.update(
            post,
            title=title,
            content=content,
            contact=contact,
            max_members=max_members,
            status=status,
            expires_at=expires_at,
        )

    async def delete_post(
        self,
        *,
        post_id: int,
        actor_user_id: int,
    ) -> None:
        """ADMIN+ of the team can delete."""
        post = await self._repo.get_by_id(post_id)
        if post is None:
            raise NotFoundError(
                "Recruitment post not found",
                data={"type": "recruitment_post", "id": post_id},
            )
        if not await team_seats.may(
            self._team_repo._session,
            post.team_id,
            actor_user_id,
            TeamPermission.recruit,
        ):
            raise ForbiddenError(
                "Only team admins or owner can delete recruitment posts"
            )
        await self._repo.soft_delete(post)
