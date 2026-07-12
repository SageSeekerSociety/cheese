import uuid
from collections.abc import Sequence
from typing import Protocol

from app.core.errors import NotFoundError
from app.domain.cx_notification.dto import ResolvedEntityInfoDTO
from app.domain.project.services import ProjectService
from app.domain.team.services import TeamService
from app.domain.user.services import UserService


class EntityInfoResolver(Protocol):
    """Protocol for resolving entities referenced in notification metadata."""

    def supported_entity_type(self) -> str:
        """Return the entity type key (e.g. 'team', 'user')."""
        raise NotImplementedError

    async def resolve(
        self, entity_ids: Sequence[str]
    ) -> dict[str, ResolvedEntityInfoDTO | None]:
        """Resolve a batch of entity IDs into displayable info."""
        raise NotImplementedError


class TeamEntityResolver:
    """Resolve 'team' entities for notifications."""

    def __init__(self, team_service: TeamService, avatar_base_url: str) -> None:
        self._team_service = team_service
        self._avatar_base_url = avatar_base_url.rstrip("/")

    def supported_entity_type(self) -> str:
        return "team"

    async def resolve(
        self, entity_ids: Sequence[str]
    ) -> dict[str, ResolvedEntityInfoDTO | None]:
        numeric_ids: list[int] = []
        id_strs: list[str] = []
        for raw_id in entity_ids:
            try:
                numeric_ids.append(int(raw_id))
                id_strs.append(str(int(raw_id)))
            except (TypeError, ValueError):
                continue

        if not numeric_ids:
            return {}

        teams_by_id = await self._team_service.get_teams_by_ids(numeric_ids)

        result: dict[str, ResolvedEntityInfoDTO | None] = {}
        for raw_id in id_strs:
            tid = int(raw_id)
            team = teams_by_id.get(tid)
            if team is None:
                result[raw_id] = None
                continue

            avatar_url = None
            if getattr(team, "avatar_id", None) is not None:
                avatar_url = f"{self._avatar_base_url}/avatars/{team.avatar_id}"

            result[raw_id] = ResolvedEntityInfoDTO(
                id=str(team.id),
                type="team",
                name=team.name,
                url=f"/teams/{team.id}",
                avatarUrl=avatar_url,
                status=None,
            )

        return result


class UserEntityResolver:
    """Resolve 'user' entities for notifications."""

    def __init__(self, user_service: UserService, avatar_base_url: str) -> None:
        self._user_service = user_service
        self._avatar_base_url = avatar_base_url.rstrip("/")

    def supported_entity_type(self) -> str:
        return "user"

    async def resolve(
        self, entity_ids: Sequence[str]
    ) -> dict[str, ResolvedEntityInfoDTO | None]:
        numeric_ids: list[int] = []
        id_strs: list[str] = []
        for raw_id in entity_ids:
            try:
                numeric_ids.append(int(raw_id))
                id_strs.append(str(int(raw_id)))
            except (TypeError, ValueError):
                continue

        if not numeric_ids:
            return {}

        profiles_by_user_id = await self._user_service.get_users_by_ids(numeric_ids)

        result: dict[str, ResolvedEntityInfoDTO | None] = {}
        for raw_id in id_strs:
            uid = int(raw_id)
            profile = profiles_by_user_id.get(uid)
            if profile is None:
                result[raw_id] = None
                continue

            avatar_url = f"{self._avatar_base_url}/avatars/{profile.avatar_id}"

            result[raw_id] = ResolvedEntityInfoDTO(
                id=str(uid),
                type="user",
                name=profile.nickname,
                url=f"/users/{uid}",
                avatarUrl=avatar_url,
                status=None,
            )

        return result


class ProjectEntityResolver:
    """Resolve 'project' entities for notifications."""

    def __init__(self, project_service: ProjectService) -> None:
        self._project_service = project_service

    def supported_entity_type(self) -> str:
        return "project"

    async def resolve(
        self, entity_ids: Sequence[str]
    ) -> dict[str, ResolvedEntityInfoDTO | None]:
        # cheesex projects are UUID-keyed (spec: Project = git repo, uuid PK).
        result: dict[str, ResolvedEntityInfoDTO | None] = {}
        for raw_id in entity_ids:
            try:
                project_uuid = uuid.UUID(raw_id)
            except (TypeError, ValueError):
                continue

            try:
                project = await self._project_service.get_or_404(project_uuid)
            except NotFoundError:
                result[raw_id] = None
                continue

            result[raw_id] = ResolvedEntityInfoDTO(
                id=str(project.id),
                type="project",
                name=project.name,
                url=f"/projects/{project.id}",
                avatarUrl=None,
                status=None,
            )

        return result
