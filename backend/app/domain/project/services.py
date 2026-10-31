"""Project business logic."""

import logging
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.delivery_events import SettleCard
from app.domain.project.address import Numbered, take_number
from app.domain.project.environment import project_environment
from app.domain.project.forge import PreparedRepository
from app.domain.project.member_settings import MEMBER_SETTINGS, SettingChange
from app.domain.project.models import (
    AiMode,
    Project,
    ProjectGitInstallation,
    ProjectStarterPush,
)
from app.domain.project.ports import (
    Claim,
    ComputeCredits,
    DefaultTeammate,
    SeatMentors,
    SeedOverview,
    StarterSource,
    TaskBrief,
    TaskTerms,
)
from app.domain.project.repositories import (
    ProjectGitInstallationRepository,
    ProjectRepository,
)
from app.domain.team.services import team_service
from app.domain.topic.models import TopicKind
from app.domain.topic.ports import (
    ArchivedCards,
    CloseRoomTasks,
    PlatformLine,
    SessionStore,
)
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.models import User

logger = logging.getLogger(__name__)


class ClaimInProgressError(ConflictError):
    """This team's claim of this task is being opened by another request right
    now; asking again in a moment finds its project."""

    retryable = True


@dataclass(frozen=True)
class ClaimedRepository:
    """What a claim settled before taking its lock: the repository it made on
    the forge, and the starter code that repository is to begin with."""

    repository: PreparedRepository
    starter: StarterSource | None


def _intent_brief(intent: str) -> str:
    """把用户写的那句话拼成新项目目标的开头；没写就返回空串，调用方不写任何东西。

    纯模板，不调模型——``seed_overview`` 的约定是「平台把已有的文字搬个地方」，
    所以它署 system 而不是芝士（见 ``app.domain.living_doc.overview``）。这也
    是赛题报名那条路径的形状：简报是拼出来的，房间里的第一句人话仍由人来说。
    """
    text = intent.strip()
    if not text:
        return ""
    return f"## 要做成什么\n\n{text}\n\n下一步：补上什么时候做成、这阶段不做什么。"


class ProjectArchivedError(ConflictError):
    """A write to a project its owner has archived. The class name travels in
    the error body, which is what the web client keys on to show the archived
    state instead of a passing error."""

    def __init__(self) -> None:
        super().__init__(say("projectArchived"))


def _can_cache(session: object) -> bool:
    return isinstance(session, AsyncSession)


async def refuse_writes_if_archived(
    session: AsyncSession, project_id: uuid.UUID
) -> None:
    """Raise :class:`ProjectArchivedError` when this project is archived."""
    if _can_cache(session):
        project = await ProjectRepository(session).get(project_id)
        archived_at = project.archived_at if project is not None else None
    else:
        archived_at = await session.scalar(
            select(Project.archived_at).where(Project.id == project_id)
        )
    if archived_at is not None:
        raise ProjectArchivedError()


class ProjectService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settle_card: SettleCard | None = None,
        default_teammate: DefaultTeammate | None = None,
        compute_credits: ComputeCredits | None = None,
        overview: SeedOverview | None = None,
        task_terms: TaskTerms | None = None,
        cards: ArchivedCards | None = None,
        sessions: SessionStore | None = None,
        close_tasks: CloseRoomTasks | None = None,
        events: PlatformLine | None = None,
        seat_mentors: SeatMentors | None = None,
    ):
        """``settle_card``：归档项目要把房间里等验收人的那几句话一起收掉，
        而结一张卡是 `review` 和 `delivery` 做的事，本包够不着
        （`app.domain.review.ports.SettleCard`）。不给也能构造 —— 只有归档这条
        路要它，而那条路会把 ``None`` 交给 `topic.archive_with_project` 再交给
        `review.archive`，由那里当场说清是哪个门漏了。

        ``default_teammate``：项目的芝士由本包在 ``create`` 里播种
        （`app.domain.project.ports.DefaultTeammate`），而播种是第 5 层
        `agent_instance` 的事。同样不给也能构造，理由一样：只有 ``create`` 和
        ``_accept_task_protocol`` 这两条路要它，走到那里没递进来就当场炸掉。

        ``compute_credits``：审过一道赛题报名，项目要继承机构协议里的算力，而那是
        第 3 层 `usage` 的账本（`app.domain.project.ports.ComputeCredits`）。也只有
        ``create`` 和 ``_accept_task_protocol`` 这两条路要它，同样走到那里没递进来
        就当场炸掉。

        ``cards``、``sessions``、``close_tasks`` 和 ``events``：归档项目会顺手做
        四件本包够不着的事 —— 把房间里还没结的验收卡收掉（第 4 层 `review`），把
        房间里的活结束掉（第 4 层 `room_task`），把取消归档的房间记着的会话令牌忘掉
        （第 3 层 `agent_session`），以及把「这个房间归档了」那句话落进总览
        （第 4 层 `block`）。四样都只在 ``archive`` / ``unarchive`` 这两条路上用，
        所以也不给也能构造，走到那里没递进来就当场炸掉。

        ``overview``：新项目要说的话（用户写的意图、或者这道赛题的要求）是**一份文档**
        的第一版，而文档在第 4 层 `living_doc` 里
        （`app.domain.project.ports.SeedOverview`）。``create`` 和 ``for_participation``
        这两条路要它，同样不给也能构造，走到那里没递进来就当场炸掉。

        ``task_terms``：报名这条路要读赛题那几行（赛题、谁报的名、审过之后落什么
        条款），那几行也在第 4 层（`app.domain.project.ports.TaskTerms`）。只有
        ``for_participation``、``activate_participation`` 和 ``create`` 带着赛题 id
        的那条路要它，同样不给也能构造。

        ``seat_mentors``：报名开出新项目时，赛题点名的导师要坐进去，而角色是上一层
        `membership` 的事（`app.domain.project.ports.SeatMentors`）。没递进来就不坐。"""
        self._session = session
        self._repo = ProjectRepository(session)
        self._installations = ProjectGitInstallationRepository(session)
        self._topics = TopicRepository(session)
        self._members = TopicMemberService(session)
        self._settle_card = settle_card
        self._default_teammate = default_teammate
        self._compute_credits = compute_credits
        self._overview = overview
        self._task_terms = task_terms
        self._cards = cards
        self._sessions = sessions
        self._close_tasks = close_tasks
        self._events = events
        self._seat_mentors = seat_mentors

    async def _seed_default_teammate(
        self,
        project: Project,
        *,
        display_name: str | None = None,
        type_name: str | None = None,
        pack_prompt: str | None = None,
    ) -> None:
        """这个项目的那一位芝士：没有就播一个，人设按调用方说的落上去。

        口子没递进来是接线漏了一处，当场炸掉 —— 静默留下一个没有芝士的项目更糟
        （不变量 I9b：项目出生就有芝士）。"""
        if self._default_teammate is None:  # pragma: no cover - 接线漏了才会走到
            # i18n-exempt: developer miswiring, not a refusal a reader can act on
            raise RuntimeError("default_teammate was not handed in")
        await self._default_teammate(
            self._session,
            project,
            display_name=display_name,
            type_name=type_name,
            pack_prompt=pack_prompt,
        )

    async def _has_earmark(self, project: Project, task_id: int) -> bool:
        """这个项目领过这道赛题的资源包没有。

        口子没递进来是接线漏了一处，当场炸掉 —— 静默不发资源包，赛题那边看不出
        分别，只有参与者会发现自己少了一份算力。"""
        if self._compute_credits is None:  # pragma: no cover - 接线漏了才会走到
            # i18n-exempt: developer miswiring, not a refusal a reader can act on
            raise RuntimeError("compute_credits was not handed in")
        return await self._compute_credits.has_earmark(project.id, task_id)

    async def _grant_earmark(
        self, project: Project, task_id: int, *, credits_total: float
    ) -> None:
        """把这道赛题的算力发到这个项目上，理由同 ``_has_earmark``。"""
        if self._compute_credits is None:  # pragma: no cover - 接线漏了才会走到
            # i18n-exempt: developer miswiring, not a refusal a reader can act on
            raise RuntimeError("compute_credits was not handed in")
        await self._compute_credits.grant_earmark(
            project_id=project.id,
            source_task_id=task_id,
            credits_total=credits_total,
        )

    async def _seed_overview(self, project: Project, content: str) -> None:
        """把平台已有的文字写成这个项目目标的第一版，理由同 ``_has_earmark``。"""
        if self._overview is None:  # pragma: no cover - 接线漏了才会走到
            # i18n-exempt: developer miswiring, not a refusal a reader can act on
            raise RuntimeError("overview was not handed in")
        await self._overview(self._session, project.id, content)

    def _terms(self) -> TaskTerms:
        """报名这条路上要问 `task` 的那几次，理由同 ``_has_earmark``。"""
        if self._task_terms is None:  # pragma: no cover - 接线漏了才会走到
            # i18n-exempt: developer miswiring, not a refusal a reader can act on
            raise RuntimeError("task_terms was not handed in")
        return self._task_terms

    async def channel_start(self, project: Project) -> tuple[int, dict]:
        """A new channel's number and the environment it starts with.

        Both are the project's to hand out: the counter row and the settings
        snapshot live here, and reading them from `topic`'s repositories made
        the two packages a cycle. A private chat takes the environment without
        a number — it is not addressed.
        """
        return (
            await take_number(self._session, project.id, Numbered.channel),
            project_environment(project.settings),
        )

    def room_environment(self, project: Project) -> dict:
        """The environment a private chat starts with; no number, see above."""
        return project_environment(project.settings)

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
        claimed: ClaimedRepository | None = None,
    ) -> Project:
        """Create a project and its root topic (= 项目本身, spec §6).

        Every project belongs to a team: pass ``team_id`` for a shared team; with
        None the owner's personal team is used, so a personal project is its
        team's project. With neither a team nor an owner who is a real user there
        is nowhere for the project to belong, and it is refused.

        ``intent`` is what the person said they wanted to do, when the creation
        form asked (#946 片 C). It is stored as written and, if non-empty, copied
        into the project's goals — see :func:`_intent_brief`.
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
        number, environment = await self.channel_start(project)
        root = await self._topics.add(
            project_id=project.id,
            number=number,
            environment=environment,
            title="综合",
            kind=TopicKind.root,
            created_by=owner_handle,
        )
        await self._repo.set_root_topic(project, root.id)
        # 项目的芝士和它在总览里的席位，与项目同一个事务里出生（结论 4、不变量
        # I9b）：一个项目不会有「还没有 agent」的那一刻，所以「谁答这一句」全仓
        # 只有一条席位可读。根房间先建出来，这一句才播得下席位。
        #
        # 类型（人设）落在 agent 上，不落在项目上：一个项目可以坐好几个 agent，只有
        # agent 自己知道这套人设是从哪个记忆池里说话的。这里传下去的人设总能落上
        # —— 播种刚生出来的实例本来就没有人设。
        await self._seed_default_teammate(
            project, display_name=agent_name, type_name=agent_type
        )
        # Apply task terms when eligible; a pending application gets a workspace
        # now and receives its competition resources when approved. After the
        # 芝士 above, because the terms may give it a type — and a type goes on
        # an agent that exists.
        if external_task_id is not None:
            await self._accept_task_protocol(project, external_task_id)
        # deferred-import: tests replace these names on app.domain.project.forge
        from app.domain.project.forge import bind_repository, provision_repository

        if forge_kind == "forgejo":
            # A 题目's starter code is the repository's first commit (#4767):
            # the archive as it is at this moment, so a later replacement
            # reaches only later claims. The repository is created empty and
            # the push runs after this transaction commits
            # (`task.starter_jobs`): unpacking and pushing a large archive must
            # not hold the claim open.
            if claimed is not None:
                # The claim made the repository before taking its lock.
                source = claimed.starter
                await bind_repository(self._session, claimed.repository)
            else:
                source = (
                    await self._terms().starter(external_task_id)
                    if external_task_id is not None
                    else None
                )
                await provision_repository(
                    project.id, self._session, initialize=source is None
                )
            if source is not None:
                self._session.add(
                    ProjectStarterPush(
                        project_id=project.id,
                        library_file_id=source.library_file_id,
                        revision=source.revision,
                        message=source.message,
                    )
                )
        # What the person said they wanted to do, carried into the project's
        # goals, which 综合 shows first and every AI teammate reads.
        brief = _intent_brief(intent)
        if brief:
            await self._seed_overview(project, brief)
        return project

    async def _resolve_personal_team_id(self, owner_handle: str) -> int | None:
        """owner_handle == User.username (fusion A1) → that user's personal team,
        provisioning it if needed. None when the handle isn't a real user."""
        # deferred-import: tests replace this name on app.domain.user.repositories
        from app.domain.user.repositories import UserRepository

        user = await UserRepository(session=self._session).get_by_handle(owner_handle)
        if user is None:
            return None
        team = await team_service(self._session).ensure_personal_team(user.id)
        return team.id

    async def for_participation(
        self, *, task: TaskBrief, claim: Claim, owner_handle: str
    ) -> Project:
        """Open the team's registration workspace, reusing it on reapplication.

        ``task`` and ``claim`` are the two rows this needs as values
        (`app.domain.project.ports.TaskBrief` / `Claim`): 赛题 and 报名 live one
        layer up and are handed in by whoever has them.
        """

        team_id = (
            claim.member_id
            if claim.is_team
            else await self._resolve_personal_team_id(owner_handle)
        )
        if (found := await self.claimed_project(task.id, team_id)) is not None:
            return found
        # deferred-import: tests replace these names on app.domain.project.forge
        from app.domain.project.forge import prepare_repository

        # Two teammates submitting together must not leave two projects, or two
        # repositories, for one application. This team's claim of this task is
        # taken here, until commit, before the repository is made; a second
        # claim arriving meanwhile is turned away at once (it asks again and
        # finds the project) rather than waiting on the forge behind this one.
        # No claim of another team or another task ever meets this lock.
        if not await self._repo.try_lock_claim(task.id, team_id):
            raise ClaimInProgressError(say("claimInProgress"))
        if (found := await self.claimed_project(task.id, team_id)) is not None:
            return found
        starter = await self._terms().starter(task.id)
        project_id = uuid.uuid4()
        repository = await prepare_repository(project_id, initialize=starter is None)
        project = await self.create(
            name=task.name[:200],
            owner_handle=owner_handle,
            team_id=team_id,
            external_task_id=task.id,
            project_id=project_id,
            claimed=ClaimedRepository(repository=repository, starter=starter),
        )
        # The claimer agreed to the task's mentors before claiming; they join
        # the project it opened, once — a project reused on reapplication keeps
        # whoever its people kept.
        if task.mentors and self._seat_mentors is not None:
            await self._seat_mentors(self._session, project.id, task.mentors)

        # The original requirements may be rich-text JSON. Keep their canonical
        # page reachable instead of copying serialized editor data into Markdown.
        # No deadline here: this text is written once, at claim time, before the
        # claim is approved — the participant's own deadline does not exist yet,
        # and one fixed string cannot be in every reader's time zone. The
        # overview page shows the live one beside this text.
        brief = (
            f"## 赛题要求\n\n{task.intro}\n\n"
            f"[查看完整赛题要求](/spaces/{task.space_id}/tasks/{task.id})"
        )
        await self._seed_overview(project, brief)
        return project

    async def claimed_project(
        self, task_id: int, team_id: int | None
    ) -> Project | None:
        """The project this team already opened for this task, if any."""
        if team_id is None:
            return None
        for project in await self._repo.list_for_external_task(task_id):
            if project.team_id == team_id:
                return project
        return None

    async def is_overview(self, document_id: uuid.UUID) -> bool:
        """Whether a document is some project's goals document."""
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

    async def ids_of_team(self, team_id: int) -> list[uuid.UUID]:
        """The ids of every project of this team, archived ones included."""
        return list(
            await self._session.scalars(
                select(Project.id).where(Project.team_id == team_id)
            )
        )

    async def live_of_teams(
        self, team_ids: Sequence[int]
    ) -> list[tuple[uuid.UUID, str, int]]:
        """Each project of these teams that is not archived, as (id, name,
        team id), by name."""
        rows = await self._session.execute(
            select(Project.id, Project.name, Project.team_id)
            .where(Project.team_id.in_(team_ids), Project.archived_at.is_(None))
            .order_by(Project.name)
        )
        return [(pid, name, team_id) for pid, name, team_id in rows]

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
        :meth:`ProjectRepository.merge_settings`。成员能改的那几项不走这里，走
        :meth:`change_member_settings`：每一次改动都要在综合里说出来。
        """
        if MEMBER_SETTINGS & {*patch, *remove}:
            # i18n-exempt: a call site that skipped the announcement; a developer's bug
            raise ValueError(
                "a member setting is changed through app.api.member_settings, "
                "which says the change in the project's room"
            )
        return await self._repo.merge_settings(project, patch, remove=remove)

    async def change_member_settings(
        self,
        project: Project,
        patch: Mapping[str, object],
        *,
        remove: Iterable[str] = (),
    ) -> list[SettingChange]:
        """Write settings any member may change (``member_settings``), and
        return each one this write changed, as it was and as it is now.

        ``before`` is read under the same row lock the write takes, so two
        members changing one setting at once each see what the other left.
        """
        await self._repo.lock_settings(project)
        before = dict(project.settings or {})
        after = await self._repo.merge_settings(project, patch, remove=remove)
        return [
            SettingChange(key, before.get(key), after.get(key))
            for key in sorted(MEMBER_SETTINGS & {*patch, *remove})
            if before.get(key) != after.get(key)
        ]

    async def archive(self, project_id: uuid.UUID, *, by: str) -> Project:
        """Archive the project (idempotent): it leaves its members' lists,
        writes to it are refused, and its rooms are archived with it — which is
        what stops its sessions, machines, PR polling and routines. Nothing is
        deleted.

        The project's pending invitations are withdrawn by whoever archives it,
        right after this returns: settling the invitee's notification lands on
        the delivery ledger, one layer above this package. The door does it
        (``api/routes/projects.archive_project``); ``archive`` cannot, and
        neither can ``membership``, without reaching upward.
        """
        project = await self.get_or_404(project_id)
        if project.archived_at is not None:
            return project
        # deferred-import: tests replace this name on app.domain.topic.services
        from app.domain.topic.services import TopicService

        if self._cards is None:  # pragma: no cover - 接线漏了才会走到
            # i18n-exempt: developer miswiring, not a refusal a reader can act on
            raise RuntimeError("cards was not handed in")
        if self._close_tasks is None:  # pragma: no cover - 接线漏了才会走到
            # i18n-exempt: developer miswiring, not a refusal a reader can act on
            raise RuntimeError("close_tasks was not handed in")
        if self._events is None:  # pragma: no cover - 接线漏了才会走到
            # i18n-exempt: developer miswiring, not a refusal a reader can act on
            raise RuntimeError("events was not handed in")
        project.archived_at = datetime.now(UTC)
        await TopicService(self._session).archive_with_project(
            project_id,
            by=by,
            settle_card=self._settle_card,
            cards=self._cards,
            close_tasks=self._close_tasks,
            events=self._events,
        )
        await self._session.flush()
        return project

    async def unarchive(self, project_id: uuid.UUID, *, by: str) -> Project:
        """Put the project back, with the rooms that were archived along with it.
        Withdrawn invitations stay withdrawn; they are sent again if wanted."""
        project = await self.get_or_404(project_id)
        if project.archived_at is None:
            return project
        # deferred-import: tests replace this name on app.domain.topic.services
        from app.domain.topic.services import TopicService

        if self._sessions is None:  # pragma: no cover - 接线漏了才会走到
            # i18n-exempt: developer miswiring, not a refusal a reader can act on
            raise RuntimeError("sessions was not handed in")
        if self._events is None:  # pragma: no cover - 接线漏了才会走到
            # i18n-exempt: developer miswiring, not a refusal a reader can act on
            raise RuntimeError("events was not handed in")
        await TopicService(self._session).unarchive_with_project(
            project_id, by=by, sessions=self._sessions, events=self._events
        )
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

    async def list_for_team(
        self, team_id: int, *, viewer: str | None = None
    ) -> list[Project]:
        """A team's 项目 page, newest first; a ``viewer`` does not see the ones
        they left."""
        return await self._repo.list_by_team(team_id, viewer=viewer)

    async def list_visible_to(
        self, *, handle: str | None, user_id: int | None
    ) -> list[Project]:
        """这个人有份的项目（``/projects`` 那条侧边栏列表）。

        谁算「有份」——他拥有的、在名册上的、或所属团队的——全写在
        ``ProjectRepository.list_visible_to`` 里；路由走这道门，不自己构造仓储
        （架构守卫）。
        """
        return await self._repo.list_visible_to(handle=handle, user_id=user_id)

    async def list_for_external_task(self, task_id: int) -> list[Project]:
        """这条赛题开出来的每个项目（``/projects/by-task``）。"""
        return await self._repo.list_for_external_task(task_id)

    async def get_by_team(
        self, team_id: int, *, viewer: str | None = None
    ) -> Project | None:
        """这个知是团队的那个 AI 工作台项目（P4 原生绑定），最新的一条；没有是
        None（``/projects/by-team`` 用它显示/隐藏「AI 工作台」入口）。"""
        return await self._repo.get_by_team(team_id, viewer=viewer)

    async def installation_for_project(
        self, project_id: uuid.UUID
    ) -> ProjectGitInstallation | None:
        """这个项目绑定的 GitHub 安装，没绑是 None。

        ``branch-protection`` 那一页要读它绑的仓库名才问这里，不自己构造
        ``ProjectGitInstallationRepository``（架构守卫）。
        """
        return await self._installations.get_by_project(project_id)

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
        terms_port = self._terms()
        task = await terms_port.brief(task_id)
        if task is None:
            return
        member_id = project.team_id
        if not task.is_team_submission:
            member_id = await self._session.scalar(
                select(User.id).where(User.username == project.owner_handle)
            )
        claim = await terms_port.claim(
            task_id=task_id, member_id=member_id, is_team=task.is_team_submission
        )
        # An application can prepare its workspace before approval, but cannot
        # claim the competition's resource pack while it is pending/rejected.
        #
        # 这句话以前只写了「pending/rejected 不给」，条件也跟着写成
        # `membership is not None and ...`，于是漏掉了它前半句最该挡住的那种人：
        # **压根没报名**（`membership is None`）反而一路往下，每个新项目都白拿一份
        # 资源包。过审（``ApproveType.APPROVED == 0``，口子那边折成 ``approved``）
        # 是**唯一**发放条件，所以判据读作「存在一条已通过的报名」——没有报名与没过审
        # 在这里是同一件事：不是这个赛题的参与者，就不领它的资源包。审批之后那次发放
        # 走 `activate_participation`，所以「先建工作区、过审时再拿资源」这条正路一点
        # 没变。
        if claim is None or not claim.approved:
            return
        terms = await terms_port.terms(task_id)
        # The 项目集 supplies a default agent type; a project that already picked
        # one keeps it, so accepting the protocol never overwrites a choice.
        await self._seed_default_teammate(
            project, type_name=terms.default_role, pack_prompt=terms.pack_prompt
        )
        if terms.compute_credits > 0 and not await self._has_earmark(project, task_id):
            await self._grant_earmark(
                project, task_id, credits_total=terms.compute_credits
            )

    async def activate_participation(self, *, task: TaskBrief, claim: Claim) -> None:
        """Release the task's resources to its workspace after approval."""
        if not claim.approved:
            return

        owner = None
        if not claim.is_team:
            owner = await self._session.get(User, claim.member_id)
        for project in await self._repo.list_for_external_task(task.id):
            belongs = (
                project.team_id == claim.member_id
                if claim.is_team
                else owner is not None and project.owner_handle == owner.username
            )
            if belongs:
                await self._session.execute(
                    select(Project.id)
                    .where(Project.id == project.id)
                    .with_for_update(key_share=True)
                )
                await self._accept_task_protocol(project, task.id)
