"""Project business logic."""

import uuid
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.project.models import AiMode, Project
from app.domain.project.repositories import ProjectRepository
from app.domain.task.models import Task, TaskMembership
from app.domain.topic.models import TopicKind
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.repositories import ComputeGrantRepository


class ProjectService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = ProjectRepository(session)
        self._topics = TopicRepository(session)
        self._grants = ComputeGrantRepository(session)
        self._members = TopicMemberService(session)

    async def create(
        self,
        *,
        name: str,
        owner_handle: str | None = None,
        ai_mode: AiMode = AiMode.collaborative,
        agent_type: str | None = None,
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
            team_id=team_id,
            external_task_id=external_task_id,
        )
        # Apply task terms when eligible; a pending application gets a workspace
        # now and receives its competition resources when approved.
        if external_task_id is not None:
            await self._accept_task_protocol(project, external_task_id)
        if agent_type:
            await self._set_agent_type(project, agent_type)
        await AgentInstanceService(self._session).materialize_default(project)
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
        from app.domain.team.services import team_service
        from app.domain.user.repositories import UserRepository

        user = await UserRepository(session=self._session).get_by_handle(owner_handle)
        if user is None:
            return None
        team = await team_service(self._session).ensure_personal_team(user.id)
        return team.id

    async def for_participation(
        self, *, task: Task, membership: TaskMembership, owner_handle: str
    ) -> Project:
        """Open the team's registration workspace, reusing it on reapplication."""

        # Serialize registration workspace creation for this task. Two teammates
        # submitting together must not leave two projects for one application.
        await self._session.execute(
            select(Task.id).where(Task.id == task.id).with_for_update()
        )
        team_id = (
            membership.member_id
            if membership.is_team
            else await self._resolve_personal_team_id(owner_handle)
        )
        projects = await self._repo.list_for_external_task(task.id)
        for project in projects:
            if project.team_id == team_id and team_id is not None:
                return project
        project = await self.create(
            name=task.name[:200],
            owner_handle=owner_handle,
            team_id=team_id,
            external_task_id=task.id,
        )
        from app.domain.topic.services import TopicService

        # The original requirements may be rich-text JSON. Keep their canonical
        # page reachable instead of copying serialized editor data into Markdown.
        brief = (
            f"## 赛题要求\n\n{task.intro}\n\n"
            f"[查看完整赛题要求](/spaces/{task.space_id}/tasks/{task.id})"
        )
        if task.deadline:
            deadline = task.deadline.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
            brief += f"\n\n提交截止时间：{deadline}"
        assert project.root_topic_id is not None  # create() always seeds the root.
        root = await self._topics.get(project.root_topic_id)
        assert root is not None
        await TopicService(self._session).seed_brief_doc(root, brief)
        return project

    async def _set_agent_type(self, project: Project, type_name: str) -> None:
        """Give this project's default agent a type (its persona).

        The type goes on the agent, never on the project: a project can host
        more than one agent, and only the agent knows which memory pool the
        persona is talking out of.
        """
        agents = AgentInstanceService(self._session)
        instance = await agents.materialize_default(project)
        await agents.set_type(instance, type_name)
        await self._session.flush()

    async def get(self, project_id: uuid.UUID) -> Project | None:
        """项目本身，不存在返回 None。

        给「项目在不在」这类判断用——调用方要的是分支，不是 404。别的领域想拿项目
        走这里或 :meth:`get_or_404`，不要直接构造 ``ProjectRepository``。
        """
        return await self._repo.get(project_id)

    async def team_for_project(self, project_id: uuid.UUID) -> int | None:
        """Resolve quota ownership, including older personal-team projects."""
        return await self._repo.team_for_project(project_id)

    async def get_or_404(self, project_id: uuid.UUID) -> Project:
        project = await self.get(project_id)
        if project is None:
            raise NotFoundError("Project not found")
        return project

    async def list_all(self) -> tuple[list[Project], int]:
        return await self._repo.list_all(), await self._repo.count()

    async def list_for_team(self, team_id: int) -> list[Project]:
        """A team's 项目 page. For a personal team this also folds in the owner's
        legacy team-less projects (rows created before 项目归团队), newest first."""
        from app.domain.team.services import team_service
        from app.domain.user.repositories import UserRepository

        projects = await self._repo.list_by_team(team_id)
        team = await team_service(self._session).get_team(team_id)
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

    async def _accept_task_protocol(self, project: Project, task_id: int) -> None:
        """Apply the 赛题's 机构协议 to a freshly created project (#370).

        Resolves the terms from the 赛题's 项目集 (with the 赛题's own override,
        option (c)) and does the two things accepting a protocol means: inherit
        the default expert role when the project has none, and issue the 资源包's
        compute credits restricted to this project. Shared team grants are
        managed separately and remain available to unlinked projects too.

        Best-effort: a 赛题 that has gone missing, or one whose 项目集 offers
        nothing, leaves the project exactly as it was. Creating a project must
        not fail because an institution left its resource pack empty.
        """
        from app.domain.space.models import SpaceCategory
        from app.domain.task.models import Task
        from app.domain.task.protocol import resolve

        task = await self._session.get(Task, task_id)
        if task is None:
            return
        from app.domain.task.models import TaskMembership
        from app.domain.user.models import User

        member_id = project.team_id
        if task.submitter_type == 0:
            member_id = await self._session.scalar(
                select(User.id).where(User.username == project.owner_handle)
            )
        membership = await self._session.scalar(
            select(TaskMembership)
            .where(
                TaskMembership.task_id == task_id,
                TaskMembership.member_id == member_id,
                TaskMembership.is_team == (task.submitter_type == 1),
                TaskMembership.deleted_at.is_(None),
            )
            .order_by(TaskMembership.created_at.desc())
            .limit(1)
        )
        # An application can prepare its workspace before approval, but cannot
        # claim the competition's resource pack while it is pending/rejected.
        if membership is not None and membership.approved != 0:
            return
        category = (
            await self._session.get(SpaceCategory, task.category_id)
            if getattr(task, "category_id", None)
            else None
        )
        protocol = resolve(category=category, task=task)
        # The 项目集 supplies a default agent type; a project that already picked
        # one keeps it, so accepting the protocol never overwrites a choice.
        agent = await AgentInstanceService(self._session).for_project(project)
        if protocol.default_role and agent.type_name is None:
            await self._set_agent_type(project, protocol.default_role)
        grants = await self._grants.list_for_project(project.id)
        if protocol.compute_credits > 0 and not any(
            grant.source_task_id == task_id for grant in grants
        ):
            await self._grants.grant(
                project_id=project.id,
                source_task_id=task_id,
                credits_total=protocol.compute_credits,
            )

    async def activate_participation(
        self, *, task: Task, membership: TaskMembership
    ) -> None:
        """Release the task's resources to its workspace after approval."""
        if membership.approved != 0:
            return
        from app.domain.user.models import User

        owner = None
        if not membership.is_team:
            owner = await self._session.get(User, membership.member_id)
        for project in await self._repo.list_for_external_task(task.id):
            belongs = (
                project.team_id == membership.member_id
                if membership.is_team
                else owner is not None and project.owner_handle == owner.username
            )
            if belongs:
                await self._session.execute(
                    select(Project.id).where(Project.id == project.id).with_for_update()
                )
                await self._accept_task_protocol(project, task.id)
