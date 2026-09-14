"""Resolve a room's selected agent, or the project's saved default agent."""

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.agent.harness import harness_name
from app.domain.agent_instance.configuration import (
    AgentConfiguration,
    initial_model,
    validate_configuration,
)
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_instance.repositories import AgentInstanceRepository
from app.domain.agent_type.library import preset_types
from app.domain.identity.handles import (
    CHEESE_HANDLE,
    CHEESE_NAME,
    UNRESOLVED_AGENT_HANDLE,
    topic_agent_handle,
)
from app.domain.memory.models import MemoryScope, agent_project_scope_id
from app.domain.project.models import Project
from app.domain.topic.models import Topic

# An instance handle keys a memory pool (``{project}:{handle}``), so it may not
# contain the separator, and it travels through URLs and prompts.
_HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class ResolvedAgent:
    """The agent acting in one place — configured or implicit.

    ``instance_id`` is None for a project that never picked one: 芝士 under the
    ``cheese`` handle, with no type. That case deliberately produces the SAME
    pool key an explicit ``cheese`` instance would, so configuring one later
    inherits everything the project's 芝士 already remembered rather than
    starting from an empty pool.
    """

    instance_id: uuid.UUID | None
    handle: str
    type_name: str | None
    display_name: str
    configuration: dict = field(default_factory=dict)


IMPLICIT_DEFAULT = ResolvedAgent(
    instance_id=None,
    handle=CHEESE_HANDLE,
    type_name=None,
    display_name=CHEESE_NAME,
)


def memory_pool(project_id: uuid.UUID, agent: ResolvedAgent) -> tuple[MemoryScope, str]:
    """The pool this agent's memory lives in, inside this project."""
    return MemoryScope.agent_project, agent_project_scope_id(project_id, agent.handle)


def legacy_topic_pool(
    project_id: uuid.UUID, topic_id: uuid.UUID
) -> tuple[MemoryScope, str]:
    """The pool a room's 分身 wrote to while memory was keyed by topic.

    Nothing new lands here — writes go to the agent's own pool — but a room that
    accumulated facts under its per-topic handle must keep reading them, or the
    day this shipped is the day 芝士 forgot everything it had learned in that
    room. Not migrated on purpose: merging the pools is a separate piece of work
    that has to wait for injection to stop being a flat 50-fact dump.
    """
    return MemoryScope.agent_project, agent_project_scope_id(
        project_id, topic_agent_handle(topic_id)
    )


class AgentInstanceService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = AgentInstanceRepository(session)

    # --- resolution ---------------------------------------------------------

    async def for_project(self, project: Project) -> ResolvedAgent:
        """The project's default agent."""
        if project.default_agent_instance_id is None:
            return self.resolved(await self.materialize_default(project))
        instance = await self._repo.get(project.default_agent_instance_id)
        return self.resolved(instance or await self.materialize_default(project))

    async def for_topic(self, topic: Topic, project: Project) -> ResolvedAgent:
        """The agent acting in *topic* — its own, else the project's default."""
        if topic.agent_instance_id is not None:
            instance = await self._repo.get(topic.agent_instance_id)
            if instance is not None:
                return self.resolved(instance)
        return await self.for_project(project)

    async def recipient_for_topic(
        self, topic: Topic, project: Project
    ) -> ResolvedAgent:
        """Read the message recipient without creating or activating an agent."""
        for instance_id in (topic.agent_instance_id, project.default_agent_instance_id):
            if instance_id is not None:
                instance = await self._repo.get(instance_id)
                if instance is not None:
                    return self.resolved(instance)
        instance = await self._repo.get_by_handle(
            project_id=project.id, handle=IMPLICIT_DEFAULT.handle
        )
        return self.resolved(instance) if instance is not None else IMPLICIT_DEFAULT

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
        if (
            handle == IMPLICIT_DEFAULT.handle
            and project.default_agent_instance_id is None
        ):
            # The project's 芝士, still implicit — asking to talk to it is one of
            # the ways of choosing it, same as configuring it.
            return await self.materialize_default(project)
        raise NotFoundError(f"这个项目里没有 handle 为 {handle!r} 的队友")

    async def system_prompt(self, agent: ResolvedAgent) -> str | None:
        """The role instructions saved on this agent."""
        return agent.configuration.get("body") or None

    async def harness(self, agent: ResolvedAgent) -> str:
        """The execution harness saved on this agent."""
        return harness_name(agent.configuration.get("harness"))

    async def model(self, agent: ResolvedAgent) -> str | None:
        """The explicit model saved on this agent."""
        return agent.configuration.get("model")

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
        project = await self._project(project_id)
        config = configuration or await self.initial_configuration(project, type_name)
        validate_configuration(config, project.settings)
        return await self._repo.create(
            project_id=project_id,
            handle=handle,
            type_name=type_name or None,
            display_name=display_name.strip() or CHEESE_NAME,
            configuration=config.model_dump(),
        )

    async def set_type(
        self, instance: AgentInstance, type_name: str | None
    ) -> AgentInstance:
        await self._require_known_type(type_name)
        project = await self._project(instance.project_id)
        config = await self.initial_configuration(project, type_name)
        validate_configuration(config, project.settings)
        instance.configuration = config.model_dump()
        instance.type_name = type_name or None
        return instance

    async def initial_configuration(
        self, project: Project, type_name: str | None = None
    ) -> AgentConfiguration:
        preset = preset_types().get(type_name) if type_name else None
        return AgentConfiguration(
            body=preset.body if preset else "",
            model=(preset.model if preset else None) or initial_model(project.settings),
            harness=harness_name(preset.harness if preset else None),
            skills=list(preset.skills) if preset else [],
            mcp_servers=list(preset.mcp_servers) if preset else [],
            effort=preset.effort if preset else None,
        )

    async def configure(
        self, instance: AgentInstance, config: AgentConfiguration
    ) -> None:
        project = await self._project(instance.project_id)
        validate_configuration(config, project.settings)
        instance.configuration = config.model_dump()

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
            await self._pin_private_chats(project)
            project.default_agent_instance_id = active[0].id
        await self._session.flush()

    async def set_project_default(
        self, project: Project, instance: AgentInstance | None
    ) -> ResolvedAgent:
        if instance is not None and not instance.is_active:
            raise ValidationError("这个队友已停用，不能设为默认")
        await self._pin_private_chats(project)
        project.default_agent_instance_id = instance.id if instance else None
        await self._session.flush()
        return await self.for_project(project)

    async def _pin_private_chats(self, project: Project) -> None:
        """Settle the 私聊 that name no teammate, before this project's default
        stops being the one answering them.

        A DM keyed to nobody is answered by the default, so moving the default
        would silently move the conversation too — the thing 一人一间 exists to
        prevent. Imported here rather than at module scope: the topic side reads
        this service to resolve a room's agent, and the two would import each
        other.
        """
        from app.domain.topic.services import TopicService

        await TopicService(self._session).pin_agent_dms_to_current_default(project)

    async def materialize_default(self, project: Project) -> AgentInstance:
        """The project's default as a real row, creating it if it was implicit.

        Needed by anything that has to *change* the default agent rather than
        just read it — the implicit default has nothing to edit.
        """
        if project.default_agent_instance_id is not None:
            instance = await self._repo.get(project.default_agent_instance_id)
            if instance is not None:
                return instance
        existing = await self._repo.get_by_handle(
            project_id=project.id, handle=IMPLICIT_DEFAULT.handle
        )
        instance = existing or await self._repo.create(
            project_id=project.id,
            handle=IMPLICIT_DEFAULT.handle,
            type_name=None,
            display_name=IMPLICIT_DEFAULT.display_name,
            configuration=(await self.initial_configuration(project)).model_dump(),
        )
        # Configuring the project's 芝士 is choosing it, so a retired row under
        # that handle comes back rather than becoming a default nobody may pick.
        # Same pool either way — the handle never moved.
        instance.is_active = True
        project.default_agent_instance_id = instance.id
        await self._session.flush()
        return instance

    async def _require_known_type(self, type_name: str | None) -> None:
        if type_name and type_name not in preset_types():
            raise ValidationError(f"agent 类型 {type_name!r} 不存在")

    async def _project(self, project_id: uuid.UUID) -> Project:
        # Project creation also creates its first agent, so import at call time.
        from app.domain.project.services import ProjectService

        return await ProjectService(self._session).get_or_404(project_id)

    @staticmethod
    def resolved(instance: AgentInstance) -> ResolvedAgent:
        return ResolvedAgent(
            instance_id=instance.id,
            handle=instance.handle,
            type_name=instance.type_name,
            display_name=instance.display_name or CHEESE_NAME,
            configuration=instance.configuration,
        )
