"""Custom role data access."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.expert_role.models import CustomRole


class CustomRoleRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

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
        role = CustomRole(
            name=name,
            title=title,
            description=description,
            body=body,
            space_id=space_id,
            created_by=created_by,
        )
        self._session.add(role)
        await self._session.flush()
        return role

    async def get_by_name(self, name: str) -> CustomRole | None:
        result = await self._session.execute(
            select(CustomRole).where(CustomRole.name == name)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[CustomRole]:
        result = await self._session.execute(
            select(CustomRole).order_by(CustomRole.created_at)
        )
        return list(result.scalars())

    async def delete(self, role: CustomRole) -> None:
        await self._session.delete(role)
        await self._session.flush()
