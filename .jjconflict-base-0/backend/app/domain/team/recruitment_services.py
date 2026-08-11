from datetime import datetime

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.team.models import TeamRecruitmentPost
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
        if not await self._team_repo.is_team_at_least_admin(team_id, actor_user_id):
            raise ForbiddenError(
                "Only team admins or owner can create recruitment posts"
            )
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
        if not await self._team_repo.is_team_at_least_admin(
            post.team_id, actor_user_id
        ):
            raise ForbiddenError(
                "Only team admins or owner can delete recruitment posts"
            )
        await self._repo.soft_delete(post)
