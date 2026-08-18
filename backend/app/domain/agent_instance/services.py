"""Which agent is acting here, and whose memory that makes this.

Every caller that used to ask "what handle does 芝士 write memory under in this
topic" asks :meth:`AgentInstanceService.for_topic` instead. The answer walks one
step at a time — the topic's own agent, else the project's default, else the
implicit 芝士 — so a project that has never configured anything still resolves,
without a row and without a migration.
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_instance.repositories import AgentInstanceRepository
from app.domain.agent_type.services import AgentTypeService
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
        self._types = AgentTypeService(session)

    # --- resolution ---------------------------------------------------------

    async def for_project(self, project: Project) -> ResolvedAgent:
        """The project's default agent."""
        if project.default_agent_instance_id is None:
            return IMPLICIT_DEFAULT
        instance = await self._repo.get(project.default_agent_instance_id)
        return self.resolved(instance) if instance else IMPLICIT_DEFAULT

    async def for_topic(self, topic: Topic, project: Project) -> ResolvedAgent:
        """The agent acting in *topic* — its own, else the project's default."""
        if topic.agent_instance_id is not None:
            instance = await self._repo.get(topic.agent_instance_id)
            if instance is not None:
                return self.resolved(instance)
        return await self.for_project(project)

    async def system_prompt(self, agent: ResolvedAgent) -> str | None:
        """The system prompt *agent*'s type contributes, if it has one."""
        return await self._types.system_prompt(agent.type_name)

    async def model(self, agent: ResolvedAgent) -> str | None:
        """The model *agent* runs on, or None to follow the project's pick."""
        return await self._types.model(agent.type_name)

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
        return await self._repo.create(
            project_id=project_id,
            handle=handle,
            type_name=type_name or None,
            display_name=display_name.strip() or CHEESE_NAME,
        )

    async def set_type(
        self, instance: AgentInstance, type_name: str | None
    ) -> AgentInstance:
        await self._require_known_type(type_name)
        instance.type_name = type_name or None
        return instance

    async def set_project_default(
        self, project: Project, instance: AgentInstance | None
    ) -> ResolvedAgent:
        project.default_agent_instance_id = instance.id if instance else None
        await self._session.flush()
        return await self.for_project(project)

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
        )
        project.default_agent_instance_id = instance.id
        await self._session.flush()
        return instance

    async def _require_known_type(self, type_name: str | None) -> None:
        if type_name and await self._types.resolve(type_name) is None:
            raise ValidationError(f"agent 类型 {type_name!r} 不存在")

    @staticmethod
    def resolved(instance: AgentInstance) -> ResolvedAgent:
        return ResolvedAgent(
            instance_id=instance.id,
            handle=instance.handle,
            type_name=instance.type_name,
            display_name=instance.display_name or CHEESE_NAME,
        )
