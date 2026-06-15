"""User business logic."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.user.models import User
from app.domain.user.repositories import UserRepository
from app.domain.user.schemas import UserCreate, UserUpdate


class UserService:
    def __init__(self, session: AsyncSession):
        self._repo = UserRepository(session)

    async def create(self, data: UserCreate) -> User:
        if await self._repo.get_by_handle(data.handle) is not None:
            raise ValidationError(f"Handle '{data.handle}' already taken")
        user = User(
            handle=data.handle,
            name=data.name,
            email=data.email,
            bio=data.bio,
            interests=data.interests,
            skills=data.skills,
        )
        return await self._repo.add(user)

    async def get_or_404(self, handle: str) -> User:
        user = await self._repo.get_by_handle(handle)
        if user is None:
            raise NotFoundError("User not found")
        return user

    async def list_all(self) -> tuple[list[User], int]:
        return await self._repo.list_all(), await self._repo.count()

    async def update(self, handle: str, data: UserUpdate) -> User:
        user = await self.get_or_404(handle)
        fields = data.model_dump(exclude_unset=True)
        for key, value in fields.items():
            setattr(user, key, value)
        return await self._repo.save(user)
