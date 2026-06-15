"""User data access."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.user.models import User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, user: User) -> User:
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
        return user

    async def get_by_handle(self, handle: str) -> User | None:
        stmt = select(User).where(User.handle == handle)
        return (await self._session.scalars(stmt)).first()

    async def list_all(self) -> list[User]:
        stmt = select(User).order_by(User.created_at.desc())
        return list((await self._session.scalars(stmt)).all())

    async def count(self) -> int:
        return int(
            (await self._session.scalar(select(func.count()).select_from(User))) or 0
        )

    async def save(self, user: User) -> User:
        await self._session.flush()
        await self._session.refresh(user)
        return user
