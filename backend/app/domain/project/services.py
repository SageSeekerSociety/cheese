"""Project business logic."""

import logging
import uuid
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.project.models import AiMode, Project
from app.domain.project.repositories import ProjectRepository
from app.domain.task.models import Task, TaskMembership
from app.domain.topic.models import TopicKind
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.repositories import ComputeGrantRepository

logger = logging.getLogger(__name__)


def _intent_brief(intent: str) -> str:
    """把用户写的那句话拼成新生房间的简报；没写就返回空串，调用方不写任何东西。

    纯模板，不调模型——``seed_brief_doc`` 的约定是「平台把已有的文字搬个地方」，
    所以它署 system 而不是芝士（见 ``TopicService.seed_brief_doc`` 的注释）。这也
    是赛题报名那条路径的形状：简报是拼出来的，房间里的第一句人话仍由人来说。
    """
    text = intent.strip()
    if not text:
        return ""
    return f"## 这个项目要做什么\n\n{text}\n\n下一步：在下面告诉芝士你要做什么。"


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
        agent_name: str | None = None,
        team_id: int | None = None,
        external_task_id: int | None = None,
        forge_kind: str = "forgejo",
        intent: str = "",
    ) -> Project:
        """Create a project and its root topic (= 项目本身, spec §6).

        Every project belongs to a team: pass ``team_id`` for a shared team; with
        None the owner's personal team is used, so a personal project is its
        team's project. With neither a team nor an owner who is a real user there
        is nowhere for the project to belong, and it is refused.

        ``intent`` is what the person said they wanted to do, when the creation
        form asked (#946 片 C). It is stored as written and, if non-empty, copied
        into the newborn room's document — see :func:`_intent_brief`.
        """
        owner_handle = owner_handle or None  # '' would seed a broken root roster
        if team_id is None and owner_handle:
            team_id = await self._resolve_personal_team_id(owner_handle)
        if team_id is None:
            raise ValidationError("项目需要归属一个团队")
        project = await self._repo.add(
            name=name,
            owner_handle=owner_handle,
            ai_mode=ai_mode,
            team_id=team_id,
            external_task_id=external_task_id,
            intent=intent,
        )
        project.settings = {**(project.settings or {}), "forge_kind": forge_kind}
        root = await self._topics.add(
            project_id=project.id,
            title=f"{name} · 项目总览",
            kind=TopicKind.root,
            created_by=owner_handle,
        )
        await self._repo.set_root_topic(project, root.id)
        # 项目的芝士和它在总览里的席位，与项目同一个事务里出生（结论 4、不变量
        # I9b）：一个项目不会有「还没有 agent」的那一刻，所以「谁答这一句」全仓
        # 只有一条席位可读。根房间先建出来，这一句才播得下席位。
        agents = AgentInstanceService(self._session)
        instance = await agents.materialize_default(project, display_name=agent_name)
        if agent_type:
            # 类型（人设）落在 agent 上，不落在项目上：一个项目可以坐好几个 agent，
            # 只有 agent 自己知道这套人设是从哪个记忆池里说话的。
            await agents.set_type(instance, agent_type)
            await self._session.flush()
        # Apply task terms when eligible; a pending application gets a workspace
        # now and receives its competition resources when approved. After the
        # 芝士 above, because the terms may give it a type — and a type goes on
        # an agent that exists.
        if external_task_id is not None:
            await self._accept_task_protocol(project, external_task_id)
        # 总览 = 项目本体: its roster mirrors the whole project (fusion-design §3).
        # Seed it with every current project member; 芝士 is already seated above.
        # 问的是名册那一个读法，不是人那一半：本文件在 roster() 的下游（它 import
        # ProjectService），所以这条 import 只能在函数里。
        from app.domain.membership.roster import roster

        member_handles = [m.handle for m in await roster(self._session, project.id)]
        await self._members.seed_root(
            root.id, owner_handle=owner_handle, member_handles=member_handles
        )
        from app.domain.project.forge import provision_repository

        if forge_kind == "forgejo":
            await provision_repository(project.id, self._session)
        # What the person said they wanted to do, carried into the room they are
        # about to land in. Without it the room opens empty and the only thing
        # answering 「我该说什么」 is its starter block; with it, the first thing
        # they read is their own sentence, and the next step is named.
        brief = _intent_brief(intent)
        if brief:
            from app.domain.topic.services import TopicService

            await TopicService(self._session).seed_brief_doc(root, brief)
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

    async def projects_in_space_for_owner(
        self, *, space_id: int, owner_handle: str
    ) -> list[Project]:
        """一个人在一门课里的项目 —— 课程链接问的就是这一句。

        A course hangs every student's project on ONE 课程题, and that 题 can move
        as the course grows (the teacher publishes something new). Asking by
        Space is what keeps 一学期一个项目 true across that move; asking by the
        anchor 题 would read a new 题 as 「他在这儿还没有项目」。
        """
        return await self._repo.list_for_space_and_owner(
            space_id=space_id, owner_handle=owner_handle
        )

    async def get(self, project_id: uuid.UUID) -> Project | None:
        """项目本身，不存在返回 None。

        给「项目在不在」这类判断用——调用方要的是分支，不是 404。别的领域想拿项目
        走这里或 :meth:`get_or_404`，不要直接构造 ``ProjectRepository``。
        """
        return await self._repo.get(project_id)

    async def team_for_project(self, project_id: uuid.UUID) -> int | None:
        """Resolve quota ownership, including older personal-team projects."""
        return await self._repo.team_for_project(project_id)

    async def teams_for_projects(self, projects: list[Project]) -> dict[uuid.UUID, int]:
        """每个项目 → 它的团队。

        给别的领域（usage 的批量额度汇总）调的门 —— 跨领域走 service，不摸
        对方的 repository（架构守卫）。
        """
        return await self._repo.teams_for_projects(projects)

    async def people(self, project_id: uuid.UUID) -> list[dict]:
        """这个项目里的**人**：成员行、所属小队、所有者，合成的一张表。

        名册的两个来源之一，另一个是这个项目的队友；合起来那张表由
        ``membership/roster.py`` 的 ``roster()`` 给出，它是这里唯一的调用方。想问
        「这个项目里有谁」的走那边——只读人这一半，答案里就没有队友。
        """
        return await self._repo.people(project_id)

    async def person(self, handle: str) -> dict:
        """``{name, avatar_id}`` for someone not on the roster yet, by the same
        rules a roster row follows."""
        return await self._repo.person(handle)

    async def get_or_404(self, project_id: uuid.UUID) -> Project:
        project = await self.get(project_id)
        if project is None:
            raise NotFoundError("Project not found")
        return project

    async def list_all(self) -> tuple[list[Project], int]:
        return await self._repo.list_all(), await self._repo.count()

    async def list_for_space(self, space_id: int) -> list[Project]:
        """Every project anchored on a 赛题 of this 题目版 — a course's students.

        The roster and the acceptance queue both need the whole class at once;
        going through this method keeps the project repository inside the
        project domain (architecture guard).
        """
        return await self._repo.list_for_space(space_id)

    async def list_for_team(self, team_id: int) -> list[Project]:
        """A team's 项目 page, newest first."""
        return await self._repo.list_by_team(team_id)

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
        agents = AgentInstanceService(self._session)
        instance = await agents.materialize_default(project)
        if protocol.default_role and instance.type_name is None:
            await agents.set_type(instance, protocol.default_role)
            await self._session.flush()
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
