"""知是 team-project business logic (reference: cheese-backend-nt ProjectService).

DTO shapes follow the reference/OpenAPI contract the frontend is written
against: epoch-millisecond timestamps, leader/team objects embedded, members
as {count, examples[:5]}, children nested one level under their parent.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.team.repositories import TeamRepository
from app.domain.team_project.models import Project, ProjectMemberRole, ProjectMembership
from app.domain.team_project.repositories import (
    ProjectMembershipRepository,
    ProjectRepository,
)
from app.domain.user.repositories import UserProfileRepository, UserRepository

_MAX_MEMBER_EXAMPLES = 5


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


class TeamProjectService:
    def __init__(self, session: AsyncSession) -> None:
        self._projects = ProjectRepository(session)
        self._memberships = ProjectMembershipRepository(session)
        self._teams = TeamRepository(session)
        self._users = UserRepository(session)
        self._profiles = UserProfileRepository(session)

    # --- DTO builders ------------------------------------------------------

    async def _user_payloads(self, user_ids: Sequence[int]) -> dict[int, dict]:
        ids = list(dict.fromkeys(user_ids))
        users = await self._users.get_by_ids(ids)
        profiles = await self._profiles.get_profiles_by_user_ids(ids)
        out: dict[int, dict] = {}
        for uid in ids:
            user = users.get(uid)
            profile = profiles.get(uid)
            out[uid] = {
                "id": uid,
                "username": user.username if user else "",
                "nickname": profile.nickname if profile else "",
                "avatarId": profile.avatar_id if profile else None,
                "intro": profile.intro if profile else "",
            }
        return out

    @staticmethod
    def _membership_payload(m: ProjectMembership, user_payload: dict) -> dict:
        return {
            "id": m.id,
            "user": user_payload,
            "role": m.role,
            "notes": m.notes,
            "createdAt": _ms(m.created_at),
            "updatedAt": _ms(m.updated_at),
        }

    async def _project_payloads(self, projects: Sequence[Project]) -> list[dict]:
        memberships = await self._memberships.list_by_projects([p.id for p in projects])
        by_project: dict[int, list[ProjectMembership]] = {}
        for m in memberships:
            by_project.setdefault(m.project_id, []).append(m)

        team_ids = list({p.team_id for p in projects})
        teams = await self._teams.get_by_ids(team_ids)
        user_ids = [p.leader_id for p in projects] + [m.user_id for m in memberships]
        users = await self._user_payloads(user_ids)

        payloads = []
        for p in projects:
            members = by_project.get(p.id, [])
            team = teams.get(p.team_id)
            payloads.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "colorCode": p.color_code,
                    "startDate": _ms(p.start_date),
                    "endDate": _ms(p.end_date),
                    "leader": users[p.leader_id],
                    "team": {
                        "id": p.team_id,
                        "name": team.name if team else "",
                        "intro": team.intro if team else "",
                        "avatarId": team.avatar_id if team else None,
                    },
                    "parentId": p.parent_id,
                    "externalTaskId": p.external_task_id,
                    "githubRepo": p.github_repo,
                    "archived": p.archived,
                    "createdAt": _ms(p.created_at),
                    "updatedAt": _ms(p.updated_at),
                    "members": {
                        "count": len(members),
                        "examples": [
                            self._membership_payload(m, users[m.user_id])
                            for m in members[:_MAX_MEMBER_EXAMPLES]
                        ],
                    },
                }
            )
        return payloads

    # --- guards -------------------------------------------------------------

    async def _get_or_404(self, project_id: int) -> Project:
        project = await self._projects.get_by_id(project_id)
        if project is None:
            raise NotFoundError(f"Project {project_id} not found")
        return project

    async def _require_leader(self, project: Project, actor_id: int) -> None:
        """The leader — or the parent project's leader — may mutate a project."""
        if project.leader_id == actor_id:
            return
        if project.parent_id is not None:
            parent = await self._projects.get_by_id(project.parent_id)
            if parent is not None and parent.leader_id == actor_id:
                return
        raise ForbiddenError("Only the project leader can do this")

    async def _ensure_users_exist(self, user_ids: Sequence[int]) -> None:
        ids = list(dict.fromkeys(user_ids))
        users = await self._users.get_by_ids(ids)
        missing = [uid for uid in ids if uid not in users]
        if missing:
            raise BadRequestError(f"Users not found: {missing}")

    # --- operations ----------------------------------------------------------

    async def create_project(
        self,
        *,
        actor_id: int,
        name: str,
        description: str,
        color_code: str,
        start_date_ms: int,
        end_date_ms: int,
        team_id: int,
        leader_id: int,
        parent_id: int | None = None,
        external_task_id: int | None = None,
        github_repo: str | None = None,
        member_ids: Sequence[int] = (),
        external_collaborator_ids: Sequence[int] = (),
    ) -> dict:
        team = await self._teams.get_by_id(team_id)
        if team is None:
            raise NotFoundError(f"Team {team_id} not found")
        if not await self._teams.is_team_member(team_id, actor_id):
            raise ForbiddenError("Only team members can create projects")
        await self._ensure_users_exist(
            [leader_id, *member_ids, *external_collaborator_ids]
        )
        if parent_id is not None:
            parent = await self._get_or_404(parent_id)
            if parent.team_id != team_id:
                raise BadRequestError("Parent project belongs to a different team")

        project = await self._projects.create(
            Project(
                name=name,
                description=description,
                color_code=color_code,
                start_date=_from_ms(start_date_ms),
                end_date=_from_ms(end_date_ms),
                team_id=team_id,
                leader_id=leader_id,
                parent_id=parent_id,
                external_task_id=external_task_id,
                github_repo=github_repo,
                archived=False,
            )
        )
        await self._memberships.add(
            project_id=project.id, user_id=leader_id, role=ProjectMemberRole.LEADER
        )
        for uid in dict.fromkeys(member_ids):
            if uid != leader_id:
                await self._memberships.add(
                    project_id=project.id, user_id=uid, role=ProjectMemberRole.MEMBER
                )
        for uid in dict.fromkeys(external_collaborator_ids):
            if uid != leader_id and uid not in member_ids:
                await self._memberships.add(
                    project_id=project.id, user_id=uid, role=ProjectMemberRole.EXTERNAL
                )
        return (await self._project_payloads([project]))[0]

    async def enumerate_projects(
        self,
        *,
        team_id: int,
        parent_id: int | None = None,
        leader_id: int | None = None,
        member_id: int | None = None,
        archived: bool | None = None,
    ) -> list[dict]:
        projects = await self._projects.list_by_team(
            team_id=team_id,
            parent_id=parent_id,
            leader_id=leader_id,
            member_id=member_id,
            archived=archived,
        )
        payloads = await self._project_payloads(projects)
        # one-level tree: children folded under their root (reference shape)
        roots = [p for p in payloads if p["parentId"] is None]
        by_parent: dict[int, list[dict]] = {}
        for p in payloads:
            if p["parentId"] is not None:
                by_parent.setdefault(p["parentId"], []).append(p)
        for root in roots:
            root["children"] = by_parent.get(root["id"], [])
        # a filtered query may return only children (e.g. parent_id filter):
        # fall back to the flat list so they are not silently dropped
        return roots if roots or not payloads else payloads

    async def get_project(self, project_id: int) -> dict:
        project = await self._get_or_404(project_id)
        return (await self._project_payloads([project]))[0]

    async def patch_project(self, project_id: int, actor_id: int, fields: dict) -> dict:
        project = await self._get_or_404(project_id)
        await self._require_leader(project, actor_id)

        if "name" in fields:
            project.name = str(fields["name"])
        if "description" in fields:
            project.description = str(fields["description"])
        if "colorCode" in fields:
            project.color_code = str(fields["colorCode"])
        if "startDate" in fields:
            project.start_date = _from_ms(int(fields["startDate"]))
        if "endDate" in fields:
            project.end_date = _from_ms(int(fields["endDate"]))
        if "githubRepo" in fields:
            project.github_repo = fields["githubRepo"]
        if "externalTaskId" in fields:
            project.external_task_id = fields["externalTaskId"]
        if "archived" in fields:
            project.archived = bool(fields["archived"])
        if "leaderId" in fields and fields["leaderId"] is not None:
            new_leader = int(fields["leaderId"])
            if new_leader != project.leader_id:
                await self._ensure_users_exist([new_leader])
                old = await self._memberships.get(project_id, project.leader_id)
                if old is not None:
                    await self._memberships.update_role(old, ProjectMemberRole.MEMBER)
                existing = await self._memberships.get(project_id, new_leader)
                if existing is not None:
                    await self._memberships.update_role(
                        existing, ProjectMemberRole.LEADER
                    )
                else:
                    await self._memberships.add(
                        project_id=project_id,
                        user_id=new_leader,
                        role=ProjectMemberRole.LEADER,
                    )
                project.leader_id = new_leader

        await self._projects.touch(project)
        return (await self._project_payloads([project]))[0]

    async def delete_project(self, project_id: int, actor_id: int) -> None:
        project = await self._get_or_404(project_id)
        await self._require_leader(project, actor_id)
        await self._projects.soft_delete(project)

    async def get_members(self, project_id: int) -> list[dict]:
        await self._get_or_404(project_id)
        memberships = await self._memberships.list_by_project(project_id)
        users = await self._user_payloads([m.user_id for m in memberships])
        return [self._membership_payload(m, users[m.user_id]) for m in memberships]

    async def add_member(
        self,
        project_id: int,
        actor_id: int,
        *,
        user_id: int,
        role: str = ProjectMemberRole.MEMBER,
        notes: str | None = None,
    ) -> dict:
        if role == ProjectMemberRole.LEADER:
            raise ForbiddenError("Cannot add a leader to a project")
        if not ProjectMemberRole.is_valid(role):
            raise BadRequestError(f"Invalid role: {role}")
        project = await self._get_or_404(project_id)
        await self._require_leader(project, actor_id)
        await self._ensure_users_exist([user_id])

        existing = await self._memberships.get(project_id, user_id)
        if existing is not None:
            await self._memberships.update_role(existing, role, notes)
            membership = existing
        else:
            membership = await self._memberships.add(
                project_id=project_id, user_id=user_id, role=role, notes=notes
            )
        users = await self._user_payloads([user_id])
        return self._membership_payload(membership, users[user_id])

    async def remove_member(self, project_id: int, actor_id: int, user_id: int) -> None:
        project = await self._get_or_404(project_id)
        await self._require_leader(project, actor_id)
        membership = await self._memberships.get(project_id, user_id)
        if membership is None:
            raise NotFoundError(f"User {user_id} is not a member of this project")
        if membership.role == ProjectMemberRole.LEADER:
            raise ForbiddenError("Transfer leadership before removing the leader")
        await self._memberships.soft_delete(membership)
