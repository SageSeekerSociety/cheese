from collections.abc import Sequence
from datetime import UTC, datetime

from app.core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.domain.team.models import Team, TeamMemberRole, TeamUserRelation
from app.domain.team.repositories import TeamRepository


async def check_team_locking_status(session, team_id: int) -> None:
    """Check if a team is locked due to participation in tasks with locking policies.

    Mirrors NT TeamService.checkTeamLockingStatus: queries TaskMembership rows
    where the team is APPROVED in a task with LOCK_ON_APPROVAL policy and the
    completion status is still ongoing. Raises ForbiddenError if locked.
    """
    from app.domain.task.repositories import TaskMembershipRepository

    membership_repo = TaskMembershipRepository(session=session)
    locking_policies = ["LOCK_ON_APPROVAL"]
    locked = await membership_repo.find_active_locked_memberships(
        team_id=team_id,
        locking_policies=locking_policies,
    )
    if locked:
        task_names = []
        for m in locked:
            # We don't eagerly load the task, so just mention the task ID.
            task_names.append(str(m.task_id))
        msg = (
            "Team membership cannot be changed because the team is "
            f"participating in locked task(s): {', '.join(task_names)}"
        )
        raise ForbiddenError(msg)


class TeamService:
    def __init__(self, repo: TeamRepository) -> None:
        self._repo = repo

    async def get_team(self, team_id: int) -> Team | None:
        return await self._repo.get_by_id(team_id)

    async def enumerate_teams(
        self,
        *,
        query: str | None,
        limit: int,
        offset: int = 0,
    ) -> Sequence[Team]:
        return await self._repo.list_teams(query=query, limit=limit, offset=offset)

    async def get_teams_by_ids(self, ids: Sequence[int]) -> dict[int, Team]:
        return await self._repo.get_by_ids(ids)

    async def get_teams_of_user(self, user_id: int) -> Sequence[Team]:
        """Return teams joined by the given user (simplified)."""
        return await self._repo.list_teams_of_user(user_id=user_id)

    async def get_team_members(self, team_id: int) -> Sequence[TeamUserRelation]:
        """Return raw membership records for a team."""
        return await self._repo.list_members_of_team(team_id=team_id)

    async def is_team_member(self, team_id: int, user_id: int) -> bool:
        return await self._repo.is_team_member(team_id, user_id)

    async def is_team_at_least_admin(self, team_id: int, user_id: int) -> bool:
        return await self._repo.is_team_at_least_admin(team_id, user_id)

    # --- Write operations ---

    async def create_team(
        self,
        *,
        name: str,
        intro: str,
        description: str,
        avatar_id: int,
        owner_id: int,
    ) -> Team:
        if not name.strip():
            raise BadRequestError("Team name cannot be empty")
        if await self._repo.exists_by_name(name.strip()):
            # 409 + structured data so the frontend can distinguish "duplicate
            # name" from a generic 400 and show a precise message instead of
            # the catch-all "稍后重试". Mirrors how POST /spaces handles it.
            raise ConflictError(
                "Team name already exists",
                data={"field": "name", "value": name.strip()},
            )

        now = datetime.now(UTC)
        team = Team(
            name=name.strip(),
            intro=intro or "",
            description=description or "",
            avatar_id=avatar_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )

        self._repo._session.add(team)
        await self._repo._session.flush()

        owner_relation = TeamUserRelation(
            team_id=team.id,
            user_id=owner_id,
            role=TeamMemberRole.OWNER,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._repo._session.add(owner_relation)
        await self._repo._session.flush()
        return team

    async def update_team(
        self,
        *,
        team_id: int,
        actor_user_id: int,
        name: str | None = None,
        intro: str | None = None,
        description: str | None = None,
        avatar_id: int | None = None,
    ) -> Team:
        team = await self._get_team_or_error(team_id)
        actor_relation = await self._repo.get_member_relation(team_id, actor_user_id)
        if actor_relation is None or actor_relation.role not in (
            TeamMemberRole.OWNER,
            TeamMemberRole.ADMIN,
        ):
            raise ForbiddenError("Only team owner or admins can update team")

        if name is not None:
            if not isinstance(name, str):
                raise BadRequestError("Team name must be string")
            trimmed = name.strip()
            if not trimmed:
                raise BadRequestError("Team name cannot be empty")
            if trimmed != team.name and await self._repo.exists_by_name(trimmed):
                raise ConflictError(
                    "Team name already exists",
                    data={"field": "name", "value": trimmed},
                )
            team.name = trimmed
        if intro is not None:
            team.intro = intro
        if description is not None:
            team.description = description
        if avatar_id is not None:
            if not isinstance(avatar_id, int) or avatar_id <= 0:
                raise BadRequestError("avatarId must be positive integer")
            team.avatar_id = avatar_id

        team.updated_at = datetime.now(UTC)
        await self._repo._session.flush()
        return team

    async def delete_team(self, *, team_id: int, actor_user_id: int) -> None:
        team = await self._get_team_or_error(team_id)
        actor_relation = await self._repo.get_member_relation(team_id, actor_user_id)
        if actor_relation is None or actor_relation.role != TeamMemberRole.OWNER:
            raise ForbiddenError("Only the team owner can disband a team")

        members = await self._repo.list_members_of_team(team_id)
        now = datetime.now(UTC)
        for rel in members:
            rel.deleted_at = now
            rel.updated_at = now
        await self._repo._session.flush()
        await self._repo.soft_delete_team(team)

    async def remove_team_member(
        self,
        *,
        team_id: int,
        target_user_id: int,
        actor_user_id: int,
    ) -> None:
        relation = await self._repo.get_member_relation(team_id, target_user_id)
        if relation is None:
            raise NotFoundError(
                "Resource team member not found",
                data={"teamId": team_id, "userId": target_user_id},
            )

        if relation.role == TeamMemberRole.OWNER:
            raise BadRequestError(
                "Team owner cannot be removed. Transfer ownership or disband the team."
            )

        if target_user_id != actor_user_id:
            actor_relation = await self._repo.get_member_relation(
                team_id, actor_user_id
            )
            if actor_relation is None or actor_relation.role == TeamMemberRole.MEMBER:
                raise ForbiddenError("Only admins or owner can remove other members")
            if (
                actor_relation.role == TeamMemberRole.ADMIN
                and relation.role == TeamMemberRole.ADMIN
            ):
                raise ForbiddenError("Admins cannot remove other admins")

        await check_team_locking_status(self._repo._session, team_id)
        await self._repo.soft_delete_member(relation)

    async def update_team_member_role(
        self,
        *,
        team_id: int,
        target_user_id: int,
        actor_user_id: int,
        new_role: TeamMemberRole,
    ) -> None:
        relation = await self._repo.get_member_relation(team_id, target_user_id)
        if relation is None:
            raise NotFoundError(
                "Resource team member not found",
                data={"teamId": team_id, "userId": target_user_id},
            )

        if relation.role == TeamMemberRole.OWNER:
            raise BadRequestError("Use owner transfer flow to change owner role")
        if new_role == TeamMemberRole.OWNER:
            raise BadRequestError("Cannot promote directly to owner via this endpoint")

        actor_relation = await self._repo.get_member_relation(team_id, actor_user_id)
        if actor_relation is None or actor_relation.role != TeamMemberRole.OWNER:
            raise ForbiddenError("Only team owner can change member roles")

        relation.role = new_role  # type: ignore[assignment]
        relation.updated_at = datetime.now(UTC)
        await self._repo._session.flush()

    async def transfer_team_owner(
        self,
        *,
        team_id: int,
        new_owner_user_id: int,
        actor_user_id: int,
    ) -> None:
        current_owner_relation = await self._repo.get_member_relation(
            team_id, actor_user_id
        )
        if (
            current_owner_relation is None
            or current_owner_relation.role != TeamMemberRole.OWNER
        ):
            raise ForbiddenError("Only current owner can transfer ownership")

        target_relation = await self._repo.get_member_relation(
            team_id, new_owner_user_id
        )
        if target_relation is None:
            raise NotFoundError(
                "New owner must be an existing team member",
                data={"userId": new_owner_user_id},
            )

        current_owner_relation.role = TeamMemberRole.ADMIN
        current_owner_relation.updated_at = datetime.now(UTC)

        target_relation.role = TeamMemberRole.OWNER
        target_relation.updated_at = datetime.now(UTC)
        await self._repo._session.flush()

    async def _get_team_or_error(self, team_id: int) -> Team:
        team = await self._repo.get_by_id(team_id)
        if team is None:
            raise NotFoundError(
                "Resource team not found", data={"type": "team", "id": team_id}
            )
        return team
