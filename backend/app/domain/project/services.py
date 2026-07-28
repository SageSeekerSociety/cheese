"""Project business logic."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.cx_task.repositories import TaskRepository, TaskTemplateRepository
from app.domain.project.models import AiMode, Project, ProjectTaskLink
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.models import TopicKind
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.repositories import ComputeGrantRepository


class ProjectService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = ProjectRepository(session)
        self._topics = TopicRepository(session)
        self._tasks = TaskRepository(session)
        self._templates = TaskTemplateRepository(session)
        self._grants = ComputeGrantRepository(session)
        self._members = TopicMemberService(session)

    async def create(
        self,
        *,
        name: str,
        owner_handle: str | None = None,
        ai_mode: AiMode = AiMode.collaborative,
        expert_role: str | None = None,
        team_id: int | None = None,
        external_task_id: int | None = None,
    ) -> Project:
        """Create a project and its root topic (= 项目本身, spec §6).

        Every project belongs to a team (项目归团队, v4): pass ``team_id`` for a
        shared team; with None the owner's PERSONAL team is resolved (个人 =
        单人真团队), so 个人项目 is just 个人团队的项目. Only when the owner
        handle doesn't resolve to a user (agent handles, bare test fixtures)
        does the row keep the legacy ``team_id NULL``.
        """
        owner_handle = owner_handle or None  # '' would seed a broken root roster
        if team_id is None and owner_handle:
            team_id = await self._resolve_personal_team_id(owner_handle)
        project = await self._repo.add(
            name=name,
            owner_handle=owner_handle,
            ai_mode=ai_mode,
            expert_role=expert_role,
            team_id=team_id,
            external_task_id=external_task_id,
        )
        root = await self._topics.add(
            project_id=project.id,
            title=f"{name} · 项目总览",
            kind=TopicKind.root,
            created_by=owner_handle,
        )
        await self._repo.set_root_topic(project, root.id)
        # 总览 = 项目本体: its roster mirrors the whole project (fusion-design §3).
        # Seed it with every current project member + 芝士. At create-time the
        # ProjectMember rows may not exist yet (added separately); seed_root is
        # idempotent, so the owner + 芝士 are seeded now and any members already
        # present are folded in.
        # The owner belongs on the roster from the start: the project listing is
        # scoped to owner / roster / team, and a project nobody is on is a
        # project nobody can find.
        if owner_handle:
            from app.domain.membership.repositories import MemberRepository
            from app.domain.project.models import ProjectRole

            await MemberRepository(self._session).add(
                project_id=project.id,
                user_handle=owner_handle,
                role=ProjectRole.lead,
            )
        member_handles = [
            m["handle"] for m in await self._repo.list_members(project.id)
        ]
        await self._members.seed_root(
            root.id, owner_handle=owner_handle, member_handles=member_handles
        )
        return project

    async def _resolve_personal_team_id(self, owner_handle: str) -> int | None:
        """owner_handle == User.username (fusion A1) → that user's personal team,
        provisioning it if needed. None when the handle isn't a real user."""
        # Local imports: project ↔ team would otherwise be an import cycle.
        from app.domain.team.repositories import TeamRepository
        from app.domain.team.services import TeamService
        from app.domain.user.repositories import UserRepository

        user = await UserRepository(session=self._session).get_by_handle(owner_handle)
        if user is None:
            return None
        team = await TeamService(
            TeamRepository(session=self._session)
        ).ensure_personal_team(user.id)
        return team.id

    async def get_or_404(self, project_id: uuid.UUID) -> Project:
        project = await self._repo.get(project_id)
        if project is None:
            raise NotFoundError("Project not found")
        return project

    async def list_all(self) -> tuple[list[Project], int]:
        return await self._repo.list_all(), await self._repo.count()

    async def list_for_team(self, team_id: int) -> list[Project]:
        """A team's 项目 page. For a personal team this also folds in the owner's
        legacy team-less projects (rows created before 项目归团队), newest first."""
        from app.domain.team.repositories import TeamRepository
        from app.domain.user.repositories import UserRepository

        projects = await self._repo.list_by_team(team_id)
        team = await TeamRepository(session=self._session).get_by_id(team_id)
        if team is not None and team.personal_owner_user_id is not None:
            owner = await UserRepository(session=self._session).get_by_id(
                team.personal_owner_user_id
            )
            if owner is not None:
                projects = projects + await self._repo.list_personal_legacy(
                    owner.username
                )
                projects.sort(key=lambda p: p.created_at, reverse=True)
        return projects

    async def link_task(
        self, *, project_id: uuid.UUID, task_id: uuid.UUID
    ) -> ProjectTaskLink:
        """Link a project to a task = accept the Template's protocol (§4.2)."""
        project = await self.get_or_404(project_id)
        task = await self._tasks.get(task_id)
        if task is None:
            raise NotFoundError("Task not found")
        if await self._repo.get_link(project_id=project_id, task_id=task_id):
            raise ValidationError("Project already linked to this task")
        tmpl = await self._templates.get(task.template_id)
        if tmpl is not None:
            # Inherit the Template's default expert role if the project has none
            # yet (§4.2: accepting the protocol也继承默认配置).
            if not project.expert_role and tmpl.default_role:
                project.expert_role = tmpl.default_role
            # 资源包 made real (spec §9.1 机构提供算力): a compute_credits entry
            # in the template's resource_pack issues a ComputeGrant. From the
            # first grant on, the project is metered; unlinked projects stay
            # unlimited (spec §4 自治).
            credits = (tmpl.resource_pack or {}).get("compute_credits")
            if (
                isinstance(credits, int | float)
                and not isinstance(credits, bool)
                and credits > 0
            ):
                await self._grants.grant(
                    project_id=project_id,
                    source_task_id=task_id,
                    credits_total=float(credits),
                )
        return await self._repo.link_task(project_id=project_id, task_id=task_id)

    async def unlink_task(self, *, project_id: uuid.UUID, task_id: uuid.UUID) -> None:
        """退出/断开 Task 协议 (§4): remove the project↔task link."""
        await self.get_or_404(project_id)
        if not await self._repo.unlink_task(project_id=project_id, task_id=task_id):
            raise NotFoundError("Project is not linked to this task")

    async def list_links(
        self, project_id: uuid.UUID
    ) -> tuple[list[ProjectTaskLink], int]:
        await self.get_or_404(project_id)
        links = await self._repo.list_links(project_id)
        return links, len(links)
