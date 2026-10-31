"""Resolve a room's selected agent, or the project's saved default agent."""

import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.request_facts import recall, remember
from app.core.sentences import say
from app.domain.agent_instance.configuration import AgentConfiguration
from app.domain.agent_instance.models import (
    AccountConnection,
    AgentInstance,
    NameSource,
    OwnAgent,
    PersonalModelResource,
)
from app.domain.agent_instance.repositories import (
    AGENT_INSTANCE_TABLES,
    AgentInstanceRepository,
)
from app.domain.agent_type.library import AgentTypeDef, preset_types
from app.domain.device.models import DeviceClaudeLoginRow, DeviceRow
from app.domain.identity.handles import (
    CHEESE_HANDLE,
    CHEESE_NAME,
    UNRESOLVED_AGENT_HANDLE,
    agent_instance_handle,
    agent_instance_id_bounds,
)
from app.domain.identity.services import IdentityService
from app.domain.memory.models import agent_project_scope_id
from app.domain.memory.scopes import MemoryScope
from app.domain.project.models import Project
from app.domain.topic.models import Topic, is_private_chat
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.services import UsageService

# An instance handle keys a memory pool (``{project}:{handle}``), so it may not
# contain the separator, and it travels through URLs and prompts.
_HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class ResolvedAgent:
    """The agent acting in one place — always a saved row.

    ``instance_id`` is not optional: a project is created with its 芝士, so
    「已解析的 agent」 and 「一行实例」 are the same thing. There used to be a
    third state — an implicit default with no row — and it was the reason
    authorization, seating and attribution each had to ask again whether the
    agent they were holding actually existed.
    """

    instance_id: uuid.UUID
    handle: str
    type_name: str | None
    display_name: str
    # ``default`` while it still carries 「芝士」, the name it was born with;
    # screens show that in their reader's language (``models.NameSource``).
    name_source: NameSource = NameSource.human
    configuration: dict = field(default_factory=dict)
    # What a save sends back so a stale one can be refused instead of landing on
    # top of whoever wrote first (``AgentInstance.version``).
    version: int = 1


def initial_configuration(type_name: str | None = None) -> AgentConfiguration:
    """一个新 agent 的出厂设置：这个类型的人设、技能、外部工具。

    不带项目，因为它不再需要一个——它曾经要项目是为了替这个 agent 挑一个模型，
    而模型不是 agent 的属性了（结论 3）。
    """
    preset = preset_types().get(type_name) if type_name else None
    if preset is None:
        return AgentConfiguration()
    return AgentConfiguration(
        body=preset.body,
        skills=list(preset.skills),
    )


async def type_of_seat(
    db: AsyncSession, project_id: uuid.UUID, handle: str | None
) -> AgentTypeDef | None:
    """The type of the teammate acting as ``handle`` in this project, or None.

    Where a session finds its MCP servers: they belong to the type, never to
    the saved agent. ``handle`` is a session's ``agent_handle`` — a teammate's
    seat, or the shared ``cheese`` seat, which the project's default answers.
    Read without writing, since a session start asks it on every turn.
    """
    project = await db.get(Project, project_id)
    if project is None or not handle:
        return None
    service = AgentInstanceService(db)
    agent = await service.for_seat_handle(project, handle)
    type_name = agent.type_name if agent is not None else None
    if agent is None and handle == CHEESE_HANDLE and project.default_agent_instance_id:
        instance = await db.get(AgentInstance, project.default_agent_instance_id)
        type_name = instance.type_name if instance is not None else None
    return preset_types().get(type_name) if type_name else None


async def agent_stdio_servers(
    db: AsyncSession, project_id: uuid.UUID, handle: str | None
) -> dict[str, dict]:
    """The stdio servers ``handle``'s type defines, by name. Its remote ones
    are the platform's to call (`remote_mcp`)."""
    agent_type = await type_of_seat(db, project_id, handle)
    if agent_type is None:
        return {}
    return {
        name: spec
        for name, spec in agent_type.inline_servers().items()
        if not isinstance(spec.get("url"), str)
    }


async def project_types(db: AsyncSession, project_id: uuid.UUID) -> list[AgentTypeDef]:
    """The types of this project's active teammates, each once."""
    names = {
        instance.type_name
        for instance in await AgentInstanceRepository(db).list_for_project(project_id)
        if instance.is_active and instance.type_name
    }
    library = preset_types()
    return [library[name] for name in sorted(names) if name in library]


def memory_pool(project_id: uuid.UUID, agent: ResolvedAgent) -> tuple[MemoryScope, str]:
    """The pool this agent's memory lives in, inside this project."""
    return MemoryScope.agent_project, agent_project_scope_id(project_id, agent.handle)


#: The models a project can be pointed at (`agent.model_choices`), as the
#: caller hands it in. Deliberately a plain callable rather than an import of
#: the agent package: what models exist needs the gateway, the harnesses and the
#: supply policy, and reading any of them back from here is the cycle C3 counts.
ModelChoices = Callable[[dict | None], list[dict]]


class AgentInstanceService:
    def __init__(self, session: AsyncSession, *, models: ModelChoices | None = None):
        self._session = session
        self._repo = AgentInstanceRepository(session)
        #: Without one no model can be bound (`_validate_model` refuses every
        #: id); every read path here works without it.
        self._models = models

    # --- resolution ---------------------------------------------------------

    async def for_project(self, project: Project) -> ResolvedAgent:
        """The project's default agent — a row, never an implicit stand-in."""
        return self.resolved(await self.materialize_default(project))

    async def for_topic(self, topic: Topic, project: Project) -> ResolvedAgent:
        """The agent a turn in *topic* runs as, when nobody was addressed.

        A room does not have an agent: it seats members, and which of its agents
        answers is decided by who a message addresses. What a room falls back to
        is the seat its roster answers for (`resolve_agent_handle`: the project's
        default when it sits there), which is also the seat a turn the platform
        starts is addressed to and acts under. Answered by the project's default
        instead, a room that does not seat it ran the default agent under another
        teammate's seat, as a second session on that teammate's runner.

        The project's default remains the answer where no saved teammate sits.
        A private 1:1 with a teammate is the one place nobody has to address
        anybody: the room holds two seats, so the teammate is the seat that is
        not the person's.
        """
        peer = await self._dm_teammate(topic, project)
        if peer is not None:
            return peer
        seat = await TopicMemberService(self._session).addressable_agent_handle(
            topic.id, room_id=topic.id
        )
        seated = await self.for_seat_handle(project, seat)
        return seated if seated is not None else await self.for_project(project)

    async def _dm_teammate(
        self, topic: Topic, project: Project
    ) -> ResolvedAgent | None:
        """The teammate a private 1:1 is with, when it is with a saved one.

        Read off the roster, which is where a private chat's two seats live
        (结论 19): 「谁被点名」 follows from the room holding exactly two of
        them, so a DM needs no @ and no second place recording who its other
        party is.

        A DM whose peer is a room-derived seat (opened before teammates had
        seats of their own, in a project that had no saved default to name),
        and one whose roster is no longer two seats, return None and are
        answered by the project's default, as they always were.
        """
        if not is_private_chat(topic):
            return None
        seats = await TopicMemberService(self._session).private_seats(topic.id)
        if seats is None:
            return None
        return await self.for_seat_handle(project, seats[1])

    async def for_seat_handle(
        self, project: Project, handle: str | None
    ) -> ResolvedAgent | None:
        """The saved teammate that sits on rosters as ``handle``, or None.

        A seat handle is derived from the instance id, so this is the reverse
        lookup: given who is acting (the ``a`` claim of a scoped token, the
        author of a block), which agent that is. None for a person, for the
        shared ``cheese`` seat and for a room-derived seat — none of those is
        one saved teammate.
        """
        if not handle:
            return None
        for instance in await self.list_for_project(project.id):
            if agent_instance_handle(instance.id) == handle:
                return self.resolved(instance)
        return None

    async def for_task(
        self, project: Project, room: Topic, agent_handle: str | None
    ) -> ResolvedAgent:
        """The teammate working a task: the one it was given (``Task.agent_handle``,
        a seat), else its room's — the project's unless the room has its own. A
        seat no longer in the project falls back the same way: the task is
        still worked, by the one its room would give it."""
        given = await self.for_seat_handle(project, agent_handle)
        return given or await self.for_topic(room, project)

    async def for_tasks(
        self,
        project_id: uuid.UUID,
        tasks: Mapping[uuid.UUID, tuple[uuid.UUID, str | None]],
    ) -> dict[uuid.UUID, ResolvedAgent]:
        """`for_task` for a page of one project's tasks, given as task id →
        (room id, ``Task.agent_handle``), in a fixed number of reads however many
        tasks and rooms there are: the project's teammates once, and the rosters
        of the rooms whose task was given nobody still here once
        (`TopicMemberService.addressable_agent_handles`)."""
        by_seat = {
            agent_instance_handle(instance.id): instance
            for instance in await self.list_for_project(project_id)
        }
        rooms = {room for room, given in tasks.values() if given not in by_seat}
        seats = await TopicMemberService(self._session).addressable_agent_handles(
            project_id, sorted(rooms)
        )
        default: ResolvedAgent | None = None
        answer: dict[uuid.UUID, ResolvedAgent] = {}
        for task_id, (room, given) in tasks.items():
            instance = by_seat.get(given or "") or by_seat.get(seats.get(room) or "")
            if instance is not None:
                answer[task_id] = self.resolved(instance)
                continue
            if default is None:
                project = await self._session.get(Project, project_id)
                assert project is not None, "a task's project outlives it"
                default = await self.for_project(project)
            answer[task_id] = default
        return answer

    async def for_task_by_id(
        self, project_id: uuid.UUID, room_id: uuid.UUID, agent_handle: str | None
    ) -> ResolvedAgent | None:
        """`for_task`，但按 id 找那两行 —— 给够不着第 5 层的包用。

        比本包低的包（`review` 的交付落地那几条路）手里只有任务的
        ``project_id`` 和 ``room_id``，读不出 ``Project``/``Topic`` 这两行；读表
        的事留在本包做。两行缺一个就是 ``None``：那个任务无处落脚，也就没有队友
        （`app.domain.review.ports.TaskTeammate`）。"""
        project = await self._session.get(Project, project_id)
        room = await self._session.get(Topic, room_id)
        if project is None or room is None:
            return None
        return await self.for_task(project, room, agent_handle)

    async def for_topic_by_id(self, topic_id: uuid.UUID) -> ResolvedAgent:
        """`for_topic`，但按 id 找那两行 —— 给够不着第 5 层的包用。

        「这个房间的队友是谁」原来问的是第 2 层 `topic` 自己的
        `TopicService.resolve_agent`，那个方法靠 import 本包才答得出来。问法搬到
        本包来，房间那边只留下口子（`app.domain.topic.ports.RoomTeammate`）。"""
        topic = await self._session.get(Topic, topic_id)
        if topic is None:
            raise NotFoundError(say("topicNotFound"))
        project = await self._session.get(Project, topic.project_id)
        if project is None:
            raise NotFoundError(say("projectNotFound"))
        return await self.for_topic(topic, project)

    async def project_of_seat(self, handle: str) -> uuid.UUID | None:
        """Which project's teammate sits on rosters as ``handle``, or None when
        the handle is not a saved teammate's seat at all (a person, the shared
        ``cheese`` seat, a room-derived seat, a device's agent)."""
        if agent_instance_id_bounds(handle) is None:
            return None
        if isinstance(self._session, AsyncSession):
            if self._session.autoflush:
                await self._session.flush()
            key = ("seat_project", handle)
            found, cached = recall(self._session, key)
            if found:
                return cached
            for obj in self._session.sync_session.identity_map.values():
                if (
                    isinstance(obj, AgentInstance)
                    and agent_instance_handle(obj.id) == handle
                ):
                    remember(
                        self._session,
                        key,
                        obj.project_id,
                        tables=AGENT_INSTANCE_TABLES,
                    )
                    return obj.project_id
                if (
                    isinstance(obj, Project)
                    and obj.default_agent_instance_id is not None
                    and agent_instance_handle(obj.default_agent_instance_id) == handle
                ):
                    remember(
                        self._session,
                        key,
                        obj.id,
                        tables=AGENT_INSTANCE_TABLES | frozenset({"projects"}),
                    )
                    return obj.id
            pid = await self._repo.project_of_seat_handle(handle)
            remember(
                self._session,
                key,
                pid,
                tables=AGENT_INSTANCE_TABLES,
            )
            return pid
        return await self._repo.project_of_seat_handle(handle)

    async def for_handle(self, project: Project, handle: str | None) -> AgentInstance:
        """The saved teammate a caller named by handle, else the project default.

        Used where a person picks WHICH teammate rather than inheriting one — a
        私聊 is the only such place today. Unknown handles raise instead of
        falling back to the default: quietly answering as somebody else is worse
        than saying the teammate is not here.
        """
        if handle is None:
            return await self.materialize_default(project)
        instance = await self._repo.get_by_handle(project_id=project.id, handle=handle)
        if instance is not None:
            return instance
        if handle == CHEESE_HANDLE and project.default_agent_instance_id is None:
            # 迁移窗口：旧镜像建的项目还没有芝士这一行。就地补种，走的是同一个
            # 播种函数，不是第二条读路径。
            return await self.materialize_default(project)
        raise NotFoundError(say("teammateHandleNotFound", handle=repr(handle)))

    async def for_handle_in(
        self, project_id: uuid.UUID, handle: str | None
    ) -> AgentInstance:
        """`for_handle`，但按 id 找项目那一行 —— 给够不着第 5 层的包用。

        `for_handle` 收的是 ``Project`` 行，那是本包在第 5 层读第 2 层的模型；反过来
        那些包读不出自己手上的 id 是哪一行，所以这套问法留在本包（口子在
        `app.domain.topic.ports.NamedTeammate`）。"""
        project = await self._session.get(Project, project_id)
        if project is None:
            raise NotFoundError(say("projectNotFound"))
        return await self.for_handle(project, handle)

    async def for_seat_handle_in(
        self, project_id: uuid.UUID, handle: str | None
    ) -> ResolvedAgent | None:
        """`for_seat_handle`，但按 id 找项目那一行 —— 给只拿得到 id 的调用方（路由层
        为一句给人看的话叫出队友的名字）。项目不在了就是 None，和不是队友同一个答案。"""
        project = await self._session.get(Project, project_id)
        if project is None:
            return None
        return await self.for_seat_handle(project, handle)

    async def system_prompt(self, agent: ResolvedAgent) -> str | None:
        """The role instructions saved on this agent."""
        return agent.configuration.get("body") or None

    # --- management ---------------------------------------------------------

    async def list_for_project(self, project_id: uuid.UUID) -> list[AgentInstance]:
        return await self._repo.list_for_project(project_id)

    async def list_team(self, project_id: uuid.UUID) -> list[AgentInstance]:
        """All project teammates, including those using team connections."""
        return await self._repo.list_for_project(project_id)

    async def get_in_project(
        self, *, project_id: uuid.UUID, instance_id: uuid.UUID
    ) -> AgentInstance:
        """An instance, checked to belong to *project_id*.

        The check is the point: an instance id is the key to a memory pool, so
        accepting one from another project would let a caller point this
        project's topic at somebody else's memory.
        """
        instance = await self._repo.get(instance_id)
        if instance is None or instance.project_id != project_id:
            raise NotFoundError(say("agentNotFound"))
        return instance

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        handle: str,
        type_name: str | None,
        display_name: str,
        configuration: AgentConfiguration | None = None,
    ) -> AgentInstance:
        handle = handle.strip()
        if not _HANDLE_RE.match(handle):
            raise ValidationError(say("agentHandleFormat"))
        if handle == UNRESOLVED_AGENT_HANDLE:
            raise ValidationError(say("agentHandleReserved", handle=repr(handle)))
        if await self._repo.get_by_handle(project_id=project_id, handle=handle):
            raise ValidationError(say("agentHandleTaken", handle=repr(handle)))
        await self._require_known_type(type_name)
        config = configuration or initial_configuration(type_name)
        await self._validate_model(project_id, config)
        name = display_name.strip()
        instance = await self._repo.create(
            project_id=project_id,
            handle=handle,
            type_name=type_name or None,
            display_name=name or CHEESE_NAME,
            name_source=NameSource.human if name else NameSource.default,
            configuration=config.model_dump(),
        )
        await self.ensure_identity(instance)
        return instance

    async def ensure_identity(self, instance: AgentInstance) -> str:
        """Give this agent the identity it acts under, and return its handle.

        An agent is a collaborator: it can be seated in a room, attributed to,
        and de-authorized by deleting that seat. All of that needs a user row of
        its own, derived from the agent rather than from any room it works in.

        Idempotent, and called on read as well as on create, so an agent that
        existed before this did gets its identity the next time anything asks —
        no alembic chain to fork, the same way a room's seat migrates itself.
        """

        user = await IdentityService(self._session).ensure_instance_agent_user(
            instance.id, instance.display_name
        )
        return user.username

    async def set_type(
        self, instance: AgentInstance, type_name: str | None
    ) -> AgentInstance:
        await self._require_known_type(type_name)
        instance.configuration = initial_configuration(type_name).model_dump()
        instance.type_name = type_name or None
        # The whole configuration was just replaced, so this is a write like any
        # other: a save that read the old version must not be allowed to land on
        # top of it.
        instance.version = instance.version + 1
        return instance

    async def configure(
        self,
        instance: AgentInstance,
        config: AgentConfiguration,
        *,
        expected_version: int | None = None,
    ) -> None:
        """Replace this agent's saved configuration.

        ``expected_version`` is the version the caller read. Given, the write
        happens only while the row is still at it: an agent's ``configuration``
        is one JSON replaced whole, so last-write-wins quietly drops the first
        of two people editing one teammate's skills. A stale version raises
        ``ConflictError`` carrying the version that is current, so the screen
        can say what happened and re-read. Omitted, the write is unconditional —
        what every caller that is not a person's own save already means.

        The check is the ``WHERE`` clause, not an ``if`` above the write: two
        writers that read the same version would both pass a comparison made
        here (``living_doc.repositories.set_content`` takes the same stand).
        """
        await self._validate_model(instance.project_id, config, instance.id)
        dumped = config.model_dump()
        now = datetime.now(UTC)
        if expected_version is None:
            instance.configuration = dumped
            # The attribute, not the SQL expression below: assigning the
            # expression here would put a BinaryExpression where an int lives.
            instance.version = instance.version + 1
            instance.updated_at = now
            return
        written = await self._session.scalar(
            update(AgentInstance)
            .where(
                AgentInstance.id == instance.id,
                AgentInstance.version == expected_version,
            )
            .values(
                configuration=dumped,
                version=AgentInstance.version + 1,
                updated_at=now,
            )
            .returning(AgentInstance.id)
        )
        if written is None:
            # Somebody wrote between the read and now. Re-read the row so the
            # caller (and the screen behind it) learns the version that won.
            await self._session.refresh(instance)
            raise ConflictError(
                say("agentSkillsStale"), data={"version": instance.version}
            )
        instance.configuration = dumped
        instance.version = expected_version + 1
        instance.updated_at = now

    async def append_to_persona(self, instance: AgentInstance, text: str) -> None:
        """Add a paragraph to the end of this teammate's persona (``body``):
        the 资源包's prompt, at project creation (``adapters.default_teammate``)."""
        config = AgentConfiguration.model_validate(instance.configuration or {})
        config.body = "\n\n".join(part for part in (config.body, text) if part)
        await self.configure(instance, config)

    async def _validate_model(
        self,
        project_id: uuid.UUID,
        config: AgentConfiguration,
        instance_id: uuid.UUID | None = None,
    ) -> None:
        project = await self._session.get(Project, project_id)
        if project is None:
            raise NotFoundError(say("projectNotFound"))
        if config.execution_device_id:
            device = await self._session.get(DeviceRow, config.execution_device_id)
            if device is None or device.team_id != project.team_id:
                raise ValidationError(say("deviceNotInProject"))
        if config.model is None:
            # 这次保存没碰模型（只改了技能之类）：没有要校验的选择，自己的队友
            # 也一样 —— 「保留原来的模型」不该被当成「选了一个不被允许的模型」。
            return
        own = await self._session.get(OwnAgent, instance_id) if instance_id else None
        if own is not None:
            if own.account_connection_id:
                table, field, connection_id = (
                    AccountConnection,
                    PersonalModelResource.account_connection_id,
                    own.account_connection_id,
                )
            else:
                table, field, connection_id = (
                    DeviceClaudeLoginRow,
                    PersonalModelResource.login_id,
                    own.login_id,
                )
            connection = (
                await self._session.get(table, connection_id) if connection_id else None
            )
            reference = await self._session.scalar(
                select(PersonalModelResource.id).where(
                    PersonalModelResource.team_id == project.team_id,
                    field == connection_id,
                )
            )
            available = (
                connection is not None
                and (connection.team_id == project.team_id or reference is not None)
                and (
                    connection.owner_user_id is None
                    or connection.owner_user_id == own.owner_user_id
                )
            )
            if isinstance(connection, DeviceClaudeLoginRow):
                available = (
                    available and connection.logged_in and connection.revoked_at is None
                )
            models = (
                connection.allowed_models
                if available and connection is not None
                else []
            )
            if config.model not in models:
                raise ValidationError(say("teamConnectionModelRefused"))
            return
        choices = self._models(project.settings) if self._models else []
        choice = next((c for c in choices if c["id"] == config.model), None)
        access = await UsageService(self._session).model_access(project.team_id)
        if choice is None or not access.shows(choice):
            raise ValidationError(say("modelUnavailable"))
        if not access.allows(choice):
            raise ValidationError(say("modelNotInPlan", label=choice["label"]))

    async def rename(self, instance: AgentInstance, display_name: str) -> AgentInstance:
        """What this agent is called. Its ``handle`` is deliberately untouched:
        that keys the memory pool, so a rename must not move what it knows."""
        name = display_name.strip()
        if not name:
            raise ValidationError(say("agentNameRequired"))
        if len(name) > 64:
            raise ValidationError(say("agentNameTooLong"))
        instance.display_name = name
        instance.name_source = NameSource.human
        return instance

    async def deactivate(self, project: Project, instance: AgentInstance) -> None:
        """Retire an agent while preserving its identity, rooms and memory.

        New rooms need an active default, so the last active agent cannot retire.
        Retiring the default selects another active agent for new rooms.
        """
        active = [
            row
            for row in await self.list_team(project.id)
            if row.is_active and row.id != instance.id
        ]
        if not active:
            # New rooms require a saved agent, with no implicit fallback.
            raise ValidationError(say("deactivateLastTeammate"))
        instance.is_active = False
        if project.default_agent_instance_id == instance.id:
            project.default_agent_instance_id = active[0].id
        await self._session.flush()

    async def set_project_default(
        self, project: Project, instance: AgentInstance
    ) -> ResolvedAgent:
        if not instance.is_active:
            raise ValidationError(say("teammateInactiveNoDefault"))
        project.default_agent_instance_id = instance.id
        await self._session.flush()
        return self.resolved(instance)

    async def materialize_default(
        self, project: Project, *, display_name: str | None = None
    ) -> AgentInstance:
        """项目的默认 agent，没有就在这里播种——全仓唯一的播种函数。

        播种发生在两处：`ProjectService.create`（建项目时，和总览房间同一个事务），
        和这里被读到一个还没有实例行的项目时的就地补种。补种不是第二条读路径，它
        建的是真行、走的是这一个函数，所以「这个项目的芝士是谁」仍然只有一处声明。

        补种留着是因为迁移窗口：回填跑完之后、容器换掉之前，旧镜像还在建不带实例
        行的项目。若这里变成一次「必然命中」的纯查询，那些项目换容器后直接炸。

        席位和实例一起播：一行实例没有席位，就是一个进不了房间的参与者，授权也就
        没有可读的那一行。`ProjectService.create` 先建总览房间再调这里，所以正常
        路径上总有一个房间可坐；直接拼出来、还没有总览房间的 Project 只拿到实例。

        席位只在实例出生这一次播，读到一行已有的实例不补席位——补了就等于「撤掉
        席位」在下一次读的时候自动撤销，而那一撤正是席位存在的理由（见
        `TopicMemberService.holds_an_agent_seat`）。旧镜像在迁移窗口里建的项目
        （有实例行、有指针、没有席位）因此不由这里接住，由 `b4d1a70c9e52` 那条
        幂等回填在 P11 的迁移里原样再跑一遍接住。
        """
        if project.default_agent_instance_id is not None:
            instance = await self._repo.get(project.default_agent_instance_id)
            if instance is not None:
                return instance
        existing = await self._repo.get_by_handle(
            project_id=project.id, handle=CHEESE_HANDLE
        )
        instance = existing or await self._repo.create(
            project_id=project.id,
            handle=CHEESE_HANDLE,
            type_name=None,
            display_name=CHEESE_NAME,
            name_source=NameSource.default,
            configuration=initial_configuration().model_dump(),
        )
        if display_name is not None:
            await self.rename(instance, display_name)
        # An agent gets its identity when it comes into being, and the project's
        # 芝士 comes into being here rather than in create(). Without it the
        # project's own room could not seat it under its own seat.
        await self.ensure_identity(instance)
        # Configuring the project's 芝士 is choosing it, so a retired row under
        # that handle comes back rather than becoming a default nobody may pick.
        # Same pool either way — the handle never moved.
        instance.is_active = True
        project.default_agent_instance_id = instance.id
        if project.root_topic_id is not None:
            seat = agent_instance_handle(instance.id)
            if existing is None and isinstance(self._session, AsyncSession):
                if self._session.autoflush:
                    await self._session.flush()
                remember(
                    self._session,
                    ("topic_member_row", project.root_topic_id, seat),
                    None,
                    tables=frozenset({"topic_memberships"}),
                )
            await TopicMemberService(self._session).ensure_agent_seat(
                project.root_topic_id, seat
            )
        await self._session.flush()
        return instance

    async def _require_known_type(self, type_name: str | None) -> None:
        if type_name and type_name not in preset_types():
            raise ValidationError(say("agentTypeNotFound", type=repr(type_name)))

    @staticmethod
    def resolved(instance: AgentInstance) -> ResolvedAgent:
        return ResolvedAgent(
            instance_id=instance.id,
            handle=instance.handle,
            type_name=instance.type_name,
            display_name=instance.display_name or CHEESE_NAME,
            name_source=(
                NameSource(instance.name_source)
                if instance.display_name
                else NameSource.default
            ),
            configuration=instance.configuration,
            version=instance.version,
        )
