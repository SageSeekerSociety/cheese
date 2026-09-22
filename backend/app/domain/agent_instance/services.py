"""Resolve a room's selected agent, or the project's saved default agent."""

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.agent_instance.configuration import AgentConfiguration
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_instance.repositories import AgentInstanceRepository
from app.domain.agent_type.library import preset_types
from app.domain.identity.handles import (
    CHEESE_HANDLE,
    CHEESE_NAME,
    UNRESOLVED_AGENT_HANDLE,
    agent_instance_handle,
)
from app.domain.memory.models import MemoryScope, agent_project_scope_id
from app.domain.project.models import Project
from app.domain.topic.models import Topic
from app.domain.topic_membership.services import TopicMemberService

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
    configuration: dict = field(default_factory=dict)


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
        mcp_servers=list(preset.mcp_servers),
    )


def memory_pool(project_id: uuid.UUID, agent: ResolvedAgent) -> tuple[MemoryScope, str]:
    """The pool this agent's memory lives in, inside this project."""
    return MemoryScope.agent_project, agent_project_scope_id(project_id, agent.handle)


class AgentInstanceService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = AgentInstanceRepository(session)

    # --- resolution ---------------------------------------------------------

    async def for_project(self, project: Project) -> ResolvedAgent:
        """The project's default agent — a row, never an implicit stand-in."""
        return self.resolved(await self.materialize_default(project))

    async def for_topic(self, topic: Topic, project: Project) -> ResolvedAgent:
        """The agent a turn in *topic* runs as, when nobody was addressed.

        A room does not have an agent: it seats members, and which of its agents
        answers is decided by who a message addresses. What a room falls back to
        is the project's default. A private 1:1 with a teammate is the one place
        nobody has to address anybody: the room holds two seats, so the teammate
        is the seat that is not the person's.
        """
        peer = await self._dm_teammate(topic, project)
        if peer is not None:
            return peer
        return await self.for_project(project)

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
        if not topic.is_private:
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

    async def project_of_seat(self, handle: str) -> uuid.UUID | None:
        """Which project's teammate sits on rosters as ``handle``, or None when
        the handle is not a saved teammate's seat at all (a person, the shared
        ``cheese`` seat, a room-derived seat, a device's agent)."""
        for instance in await self._repo.list_all():
            if agent_instance_handle(instance.id) == handle:
                return instance.project_id
        return None

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
        raise NotFoundError(f"这个项目里没有 handle 为 {handle!r} 的队友")

    async def system_prompt(self, agent: ResolvedAgent) -> str | None:
        """The role instructions saved on this agent."""
        return agent.configuration.get("body") or None

    # --- management ---------------------------------------------------------

    async def list_for_project(self, project_id: uuid.UUID) -> list[AgentInstance]:
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
            raise NotFoundError("agent 不存在")
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
            raise ValidationError(
                "agent handle 只能包含小写字母、数字和 .-_，且以字母或数字开头"
            )
        if handle == UNRESOLVED_AGENT_HANDLE:
            raise ValidationError(
                f"{handle!r} 是「认不出是谁」的占位身份，不能拿来命名 agent"
            )
        if await self._repo.get_by_handle(project_id=project_id, handle=handle):
            raise ValidationError(f"这个项目里已经有 handle 为 {handle!r} 的 agent")
        await self._require_known_type(type_name)
        config = configuration or initial_configuration(type_name)
        await self._validate_model(project_id, config)
        instance = await self._repo.create(
            project_id=project_id,
            handle=handle,
            type_name=type_name or None,
            display_name=display_name.strip() or CHEESE_NAME,
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
        from app.domain.identity.services import IdentityService

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
        return instance

    async def configure(
        self, instance: AgentInstance, config: AgentConfiguration
    ) -> None:
        await self._validate_model(instance.project_id, config)
        instance.configuration = config.model_dump()

    async def _validate_model(
        self, project_id: uuid.UUID, config: AgentConfiguration
    ) -> None:
        if config.model is None:
            return
        from app.domain.agent_instance.configuration import model_choices

        project = await self._session.get(Project, project_id)
        if project is None:
            raise NotFoundError("Project not found")
        if config.model not in {item["id"] for item in model_choices(project.settings)}:
            raise ValidationError("当前项目无法使用该模型，请选择可用模型")

    async def rename(self, instance: AgentInstance, display_name: str) -> AgentInstance:
        """What this agent is called. Its ``handle`` is deliberately untouched:
        that keys the memory pool, so a rename must not move what it knows."""
        name = display_name.strip()
        if not name:
            raise ValidationError("名字不能为空")
        if len(name) > 64:
            raise ValidationError("名字最多 64 个字")
        instance.display_name = name
        return instance

    async def deactivate(self, project: Project, instance: AgentInstance) -> None:
        """Retire an agent while preserving its identity, rooms and memory.

        New rooms need an active default, so the last active agent cannot retire.
        Retiring the default selects another active agent for new rooms.
        """
        active = [
            row
            for row in await self._repo.list_for_project(project.id)
            if row.is_active and row.id != instance.id
        ]
        if not active:
            # New rooms require a saved agent, with no implicit fallback.
            raise ValidationError("请先创建另一个队友，再停用这个队友")
        instance.is_active = False
        if project.default_agent_instance_id == instance.id:
            project.default_agent_instance_id = active[0].id
        await self._session.flush()

    async def set_project_default(
        self, project: Project, instance: AgentInstance
    ) -> ResolvedAgent:
        if not instance.is_active:
            raise ValidationError("这个队友已停用，不能设为默认")
        project.default_agent_instance_id = instance.id
        await self._session.flush()
        return await self.for_project(project)

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
            await TopicMemberService(self._session).ensure_agent_seat(
                project.root_topic_id, agent_instance_handle(instance.id)
            )
        await self._session.flush()
        return instance

    async def _require_known_type(self, type_name: str | None) -> None:
        if type_name and type_name not in preset_types():
            raise ValidationError(f"agent 类型 {type_name!r} 不存在")

    @staticmethod
    def resolved(instance: AgentInstance) -> ResolvedAgent:
        return ResolvedAgent(
            instance_id=instance.id,
            handle=instance.handle,
            type_name=instance.type_name,
            display_name=instance.display_name or CHEESE_NAME,
            configuration=instance.configuration,
        )
