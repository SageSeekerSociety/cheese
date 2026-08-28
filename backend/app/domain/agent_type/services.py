"""Agent type business logic.

The catalog merges the preset file library (read-only) with the ``agent_types``
table. A custom type may reuse a preset name — it then shadows the preset
everywhere (catalog and resolution alike), so overriding one never means
forking its file. Presets themselves can never be edited or deleted.
"""

import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.agent.harness import HARNESSES, known_harness
from app.domain.agent_type.library import AgentTypeDef, preset_types
from app.domain.agent_type.models import AgentType
from app.domain.agent_type.repositories import AgentTypeRepository
from app.domain.agent_type.schemas import AgentTypeOut

# Type names are slugs — they are stored on agent instances and show up in
# files and URLs.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _check_harness(declared: str | None) -> None:
    """Refuse a harness this deployment cannot run — at WRITE time.

    A stored value nothing honours is exactly how this column spent its first
    months: settable, saved, and ignored on the run path. Rejecting here rather
    than when a turn starts means the mistake is answered by whoever made it,
    while they are looking at the form, instead of surfacing hours later as a
    room where 芝士 is not the agent someone configured.
    """
    if known_harness(declared):
        return
    offered = "、".join(sorted(HARNESSES))
    raise ValidationError(f"没有叫 {declared!r} 的运行方式。现在只有：{offered}")


class AgentTypeService:
    def __init__(self, session: AsyncSession):
        self._repo = AgentTypeRepository(session)

    async def resolve(self, name: str | None) -> AgentTypeDef | None:
        """The definition behind *name*: custom (DB) > preset file > None.

        One resolution order for every consumer — a caller that asked the file
        library directly would silently ignore an override someone made in the
        UI, which is the whole point of allowing shadowing.
        """
        if not name:
            return None
        row = await self._repo.get_by_name(name)
        if row is not None:
            return self._to_def(row)
        return preset_types().get(name)

    async def system_prompt(self, name: str | None) -> str | None:
        """The system prompt for *name*, or None if unknown/unset."""
        resolved = await self.resolve(name)
        return resolved.body if resolved else None

    async def model(self, name: str | None) -> str | None:
        """The model *name* runs on, or None to follow the project's pick.

        A type that names no model is not choosing "the default" — it is
        declining to choose, which is why the project's setting still applies
        under it rather than being overridden by a blank.
        """
        resolved = await self.resolve(name)
        return (resolved.model or None) if resolved else None

    async def list_merged(self) -> list[AgentTypeOut]:
        """Presets first, then custom types; a custom shadows its namesake."""
        customs = await self._repo.list_all()
        shadowed = {c.name for c in customs}
        items = [
            AgentTypeOut(
                name=t.name,
                title=t.title,
                description=t.description,
                body=t.body,
                skills=list(t.skills),
                mcp_servers=list(t.mcp_servers),
                model=t.model,
                effort=t.effort,
                harness=t.harness,
                builtin=True,
            )
            for t in preset_types().values()
            if t.name not in shadowed
        ]
        items.extend(self.to_out(c) for c in customs)
        return items

    async def create(
        self,
        *,
        name: str,
        title: str,
        description: str,
        body: str,
        skills: list[str],
        mcp_servers: list[str],
        model: str | None,
        effort: str | None,
        harness: str | None,
        space_id: int | None,
        created_by: str,
    ) -> AgentType:
        if not _NAME_RE.match(name):
            raise ValidationError(
                "类型名只能包含小写字母、数字和 .-_，且以字母或数字开头"
            )
        if await self._repo.get_by_name(name) is not None:
            raise ValidationError(f"agent 类型 {name!r} 已存在")
        _check_harness(harness)
        return await self._repo.create(
            name=name,
            title=title,
            description=description,
            body=body,
            skills=skills,
            mcp_servers=mcp_servers,
            model=model,
            effort=effort,
            harness=harness,
            space_id=space_id,
            created_by=created_by,
        )

    async def update(self, name: str, **changes) -> AgentType:
        """Apply the non-None fields of *changes* to the custom type *name*."""
        agent_type = await self._get_custom_or_raise(name)
        _check_harness(changes.get("harness"))
        for field, value in changes.items():
            if value is not None:
                setattr(agent_type, field, value)
        return agent_type

    async def delete(self, name: str) -> None:
        agent_type = await self._get_custom_or_raise(name)
        await self._repo.delete(agent_type)

    async def _get_custom_or_raise(self, name: str) -> AgentType:
        """The custom row for *name* — presets are read-only, so a name that
        only exists in the file library is rejected rather than resolved."""
        agent_type = await self._repo.get_by_name(name)
        if agent_type is not None:
            return agent_type
        if name in preset_types():
            raise ValidationError(f"内置 agent 类型 {name!r} 只读，不能修改或删除")
        raise NotFoundError(f"agent 类型 {name!r} 不存在")

    @staticmethod
    def _to_def(row: AgentType) -> AgentTypeDef:
        return AgentTypeDef(
            name=row.name,
            title=row.title,
            description=row.description,
            body=row.body,
            skills=list(row.skills or []),
            mcp_servers=list(row.mcp_servers or []),
            model=row.model,
            effort=row.effort,
            harness=row.harness,
        )

    @staticmethod
    def to_out(row: AgentType) -> AgentTypeOut:
        return AgentTypeOut(
            name=row.name,
            title=row.title,
            description=row.description,
            body=row.body,
            skills=list(row.skills or []),
            mcp_servers=list(row.mcp_servers or []),
            model=row.model,
            effort=row.effort,
            harness=row.harness,
            builtin=False,
            space_id=row.space_id,
            created_by=row.created_by,
            created_at=row.created_at,
        )
