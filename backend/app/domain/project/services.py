"""Project business logic."""

import logging
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.project.models import AiMode, Project
from app.domain.project.repositories import ProjectRepository
from app.domain.task.models import Task, TaskMembership
from app.domain.topic.models import TopicKind
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.ledger import Ledger

logger = logging.getLogger(__name__)


def _intent_brief(intent: str) -> str:
    """把用户写的那句话拼成新项目总览的开头；没写就返回空串，调用方不写任何东西。

    纯模板，不调模型——``seed_overview`` 的约定是「平台把已有的文字搬个地方」，
    所以它署 system 而不是芝士（见 ``ProjectService.seed_overview`` 的注释）。这也
    是赛题报名那条路径的形状：简报是拼出来的，房间里的第一句人话仍由人来说。
    """
    text = intent.strip()
    if not text:
        return ""
    return f"## 这个项目要做什么\n\n{text}\n\n下一步：在下方对话中说明你要做什么。"


class ProjectArchivedError(ConflictError):
    """A write to a project its owner has archived. The class name travels in
    the error body, which is what the web client keys on to show the archived
    state instead of a passing error."""

    def __init__(self) -> None:
        super().__init__(say("projectArchived"))


async def refuse_writes_if_archived(
    session: AsyncSession, project_id: uuid.UUID
) -> None:
    """Raise :class:`ProjectArchivedError` when this project is archived."""
    archived_at = await session.scalar(
        select(Project.archived_at).where(Project.id == project_id)
    )
    if archived_at is not None:
        raise ProjectArchivedError()


class ProjectService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = ProjectRepository(session)
        self._topics = TopicRepository(session)
        self._ledger = Ledger(session)
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
        project_id: uuid.UUID | None = None,
    ) -> Project:
        """Create a project and its root topic (= 项目本身, spec §6).

        Every project belongs to a team: pass ``team_id`` for a shared team; with
        None the owner's personal team is used, so a personal project is its
        team's project. With neither a team nor an owner who is a real user there
        is nowhere for the project to belong, and it is refused.

        ``intent`` is what the person said they wanted to do, when the creation
        form asked (#946 片 C). It is stored as written and, if non-empty, copied
        into the project's overview — see :func:`_intent_brief`.
        """
        owner_handle = owner_handle or None  # '' would seed a broken root roster
        if project_id is not None and (earlier := await self._repo.get(project_id)):
            # An earlier attempt committed but its answer never reached the client.
            if earlier.owner_handle != owner_handle:
                raise ConflictError(say("projectNumberTaken"))
            return earlier
        if team_id is None and owner_handle:
            team_id = await self._resolve_personal_team_id(owner_handle)
        if team_id is None:
            raise ValidationError(say("projectNeedsTeam"))
        project = await self._repo.add(
            name=name,
            owner_handle=owner_handle,
            ai_mode=ai_mode,
            team_id=team_id,
            external_task_id=external_task_id,
            intent=intent,
            project_id=project_id,
        )
        project.settings = {**(project.settings or {}), "forge_kind": forge_kind}
        root = await self._topics.add(
            project_id=project.id,
            title="综合",
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
        from app.domain.project.forge import provision_repository

        if forge_kind == "forgejo":
            await provision_repository(project.id, self._session)
        # What the person said they wanted to do, carried into the project's
        # overview, which 综合 shows first and every AI teammate reads.
        brief = _intent_brief(intent)
        if brief:
            await self.seed_overview(project, brief)
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

        # The original requirements may be rich-text JSON. Keep their canonical
        # page reachable instead of copying serialized editor data into Markdown.
        brief = (
            f"## 赛题要求\n\n{task.intro}\n\n"
            f"[查看完整赛题要求](/spaces/{task.space_id}/tasks/{task.id})"
        )
        if task.deadline:
            deadline = task.deadline.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
            brief += f"\n\n提交截止时间：{deadline}"
        await self.seed_overview(project, brief)
        return project

    async def overview_document(self, project: Project):
        """The project's overview, made empty (version 0) the first time anyone
        needs it: whoever opens, writes or comments on it needs its id first."""
        from app.domain.living_doc.services import Documents

        documents = Documents(self._session)
        if project.overview_document_id is not None:
            doc = await documents.get(project.overview_document_id)
            if doc is not None:
                return doc
        # Two first readers at once must not make two overviews. NO KEY
        # UPDATE, not FOR UPDATE: this runs inside a turn's assembly, and a
        # full row lock on the project stops every write whose foreign key
        # names the project (each block takes KEY SHARE on it) until the turn
        # commits — which deadlocked an archive that held its room's row and
        # was writing its note. Two creators still wait for each other.
        await self._session.execute(
            select(Project.id)
            .where(Project.id == project.id)
            .with_for_update(key_share=True)
        )
        await self._session.refresh(project, ["overview_document_id"])
        if project.overview_document_id is not None:
            doc = await documents.get(project.overview_document_id)
            if doc is not None:
                return doc
        doc = await documents.create(project_id=project.id)
        project.overview_document_id = doc.id
        await self._session.flush()
        return doc

    async def seed_overview(self, project: Project, content: str) -> None:
        """Write a new project's overview from text it already had: what its
        creator said they wanted, or its 赛题. Author is `system`: the platform
        moved existing words, nobody wrote them here."""
        from app.domain.block.documents import seed
        from app.domain.living_doc.services import DocumentJournal

        doc = await self.overview_document(project)
        await DocumentJournal(self._session).lock(doc.id)
        await seed(self._session, doc, content)

    async def is_overview(self, document_id: uuid.UUID) -> bool:
        """Whether a document is some project's overview."""
        return (
            await self._session.scalar(
                select(Project.id).where(Project.overview_document_id == document_id)
            )
        ) is not None

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

    async def get_projects_by_ids(
        self, project_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, Project]:
        """一批项目，按 id 索引，一次查完。

        和 :meth:`get` 是同一道门（都不筛归档），给手上已经攒了一整页外键的调用方
        用 —— 通知列表的实体解析逐条 :meth:`get` 就是一屏 N 次往返。
        """
        return await self._repo.get_by_ids(project_ids)

    async def get_or_404(self, project_id: uuid.UUID) -> Project:
        project = await self.get(project_id)
        if project is None:
            raise NotFoundError("Project not found")
        return project

    async def lock_settings(self, project: Project) -> None:
        """锁住这个项目那一行并重读它的 settings（``FOR NO KEY UPDATE``）。

        改 settings 前先走这里，合并的才是当下这一份；见
        :meth:`ProjectRepository.lock_settings`。
        """
        await self._repo.lock_settings(project)

    async def merge_settings(
        self,
        project: Project,
        patch: Mapping[str, object],
        *,
        remove: Iterable[str] = (),
    ) -> dict[str, object]:
        """改 settings 里那几个键，其余照数据库当下的样子留着。

        写 settings 的正路，改一个键的调用点都走它——见
        :meth:`ProjectRepository.merge_settings`。
        """
        return await self._repo.merge_settings(project, patch, remove=remove)

    async def archive(self, project_id: uuid.UUID, *, by: str) -> Project:
        """Archive the project (idempotent): it leaves its members' lists,
        writes to it are refused, and its rooms are archived with it — which is
        what stops its sessions, machines, PR polling and routines. Its pending
        invitations are withdrawn: an invitation to a project nobody can enter
        would sit in the invitee's inbox with no answer that means anything.
        Nothing is deleted."""
        project = await self.get_or_404(project_id)
        if project.archived_at is not None:
            return project
        from app.domain.membership.services import InvitationService
        from app.domain.topic.services import TopicService

        project.archived_at = datetime.now(UTC)
        await TopicService(self._session).archive_with_project(project_id, by=by)
        await InvitationService(self._session).revoke_all_pending(project_id)
        await self._session.flush()
        return project

    async def unarchive(self, project_id: uuid.UUID, *, by: str) -> Project:
        """Put the project back, with the rooms that were archived along with it.
        Withdrawn invitations stay withdrawn; they are sent again if wanted."""
        project = await self.get_or_404(project_id)
        if project.archived_at is None:
            return project
        from app.domain.topic.services import TopicService

        await TopicService(self._session).unarchive_with_project(project_id, by=by)
        project.archived_at = None
        await self._session.flush()
        return project

    async def list_archived_owned_by(self, handle: str) -> list[Project]:
        """The archived projects this person owns — the one place they are
        listed, so that the owner can bring one back."""
        return await self._repo.list_archived_owned_by(handle)

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
        from app.domain.task.models import Task
        from app.domain.task.teaching import protocol_for_task

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
        #
        # 这句话以前只写了「pending/rejected 不给」，条件也跟着写成
        # `membership is not None and ...`，于是漏掉了它前半句最该挡住的那种人：
        # **压根没报名**（`membership is None`）反而一路往下，每个新项目都白拿一份
        # 资源包。过审（`ApproveType.APPROVED == 0`）是**唯一**发放条件，所以判据读作
        # 「存在一条已通过的报名」——没有报名与没过审在这里是同一件事：不是这个赛题的
        # 参与者，就不领它的资源包。审批之后那次发放走 `activate_participation`，所以
        # 「先建工作区、过审时再拿资源」这条正路一点没变。
        if membership is None or membership.approved != 0:
            return
        protocol = await protocol_for_task(self._session, task)
        # The 项目集 supplies a default agent type; a project that already picked
        # one keeps it, so accepting the protocol never overwrites a choice.
        agents = AgentInstanceService(self._session)
        instance = await agents.materialize_default(project)
        if protocol.default_role and instance.type_name is None:
            await agents.set_type(instance, protocol.default_role)
            await self._session.flush()
        grants = await self._ledger.earmarks(project.id)
        if protocol.compute_credits > 0 and not any(
            grant.source_task_id == task_id for grant in grants
        ):
            await self._ledger.grant_earmark(
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
                    select(Project.id)
                    .where(Project.id == project.id)
                    .with_for_update(key_share=True)
                )
                await self._accept_task_protocol(project, task.id)
