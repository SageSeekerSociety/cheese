"""Custom role business logic (spec §8.2 自定义角色).

The catalog merges the built-in file library (read-only) with the custom_roles
table. A custom role may reuse a built-in name — it then shadows the built-in
everywhere (catalog and prompt resolution). Built-ins themselves can never be
edited or deleted through the API.
"""

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.agent.roles import builtin_roles
from app.domain.expert_role.models import CustomRole
from app.domain.expert_role.repositories import CustomRoleRepository
from app.domain.expert_role.schemas import RoleOut

# Role names are slugs — they travel as Project.expert_role / Task
# default_role strings and show up in files and URLs.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class CustomRoleService:
    def __init__(self, session: AsyncSession):
        self._repo = CustomRoleRepository(session)

    async def list_merged(self) -> list[RoleOut]:
        """Built-ins first, then customs; a custom shadows its namesake."""
        customs = await self._repo.list_all()
        shadowed = {c.name for c in customs}
        items = [
            RoleOut(
                name=r.name,
                title=r.title,
                description=r.description,
                body=r.body,
                builtin=True,
            )
            for r in builtin_roles().values()
            if r.name not in shadowed
        ]
        items.extend(self._out(c) for c in customs)
        return items

    async def create(
        self,
        *,
        name: str,
        title: str,
        description: str,
        body: str,
        space_id: uuid.UUID | None,
        created_by: str,
    ) -> CustomRole:
        if not _NAME_RE.match(name):
            raise ValidationError(
                "角色名只能包含小写字母、数字和 .-_，且以字母或数字开头"
            )
        if await self._repo.get_by_name(name) is not None:
            raise ValidationError(f"角色 {name!r} 已存在")
        return await self._repo.create(
            name=name,
            title=title,
            description=description,
            body=body,
            space_id=space_id,
            created_by=created_by,
        )

    async def update(
        self,
        name: str,
        *,
        title: str | None,
        description: str | None,
        body: str | None,
    ) -> CustomRole:
        role = await self._get_custom_or_raise(name)
        if title is not None:
            role.title = title
        if description is not None:
            role.description = description
        if body is not None:
            role.body = body
        return role

    async def delete(self, name: str) -> None:
        role = await self._get_custom_or_raise(name)
        await self._repo.delete(role)

    async def _get_custom_or_raise(self, name: str) -> CustomRole:
        """The custom row for *name* — built-ins are read-only, so a name that
        only exists in the file library is rejected rather than resolved."""
        role = await self._repo.get_by_name(name)
        if role is not None:
            return role
        if name in builtin_roles():
            raise ValidationError(f"内置角色 {name!r} 只读，不能修改或删除")
        raise NotFoundError(f"角色 {name!r} 不存在")

    @staticmethod
    def _out(role: CustomRole) -> RoleOut:
        return RoleOut(
            name=role.name,
            title=role.title,
            description=role.description,
            body=role.body,
            builtin=False,
            space_id=role.space_id,
            created_by=role.created_by,
            created_at=role.created_at,
        )

    def to_out(self, role: CustomRole) -> RoleOut:
        return self._out(role)
