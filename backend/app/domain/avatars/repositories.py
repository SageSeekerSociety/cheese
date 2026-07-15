from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.avatars.models import Avatar


class AvatarRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_type(self, avatar_type: str) -> list[Avatar]:
        stmt: Select[tuple[Avatar]] = (
            select(Avatar)
            .where(Avatar.avatar_type == avatar_type)
            .order_by(Avatar.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, avatar_id: int) -> Avatar | None:
        stmt: Select[tuple[Avatar]] = select(Avatar).where(Avatar.id == avatar_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_default(self) -> Avatar | None:
        stmt: Select[tuple[Avatar]] = (
            select(Avatar)
            .where(Avatar.avatar_type == "default")
            .order_by(Avatar.id.asc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            stmt = (
                select(Avatar)
                .where(Avatar.avatar_type == "predefined")
                .order_by(Avatar.id.asc())
                .limit(1)
            )
            result = await self._session.execute(stmt)
            row = result.scalar_one_or_none()
        return row

    async def create(self, *, url: str, name: str, avatar_type: str) -> Avatar:
        now = datetime.now(UTC)
        avatar = Avatar(
            url=url,
            name=name,
            avatar_type=avatar_type,
            created_at=now,
            usage_count=0,
        )
        self._session.add(avatar)
        await self._session.flush()
        return avatar

    async def increment_usage(self, avatar_id: int) -> None:
        avatar = await self.get_by_id(avatar_id)
        if avatar:
            avatar.usage_count += 1
            await self._session.flush()
