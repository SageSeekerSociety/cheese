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

    # The AI's identity and platform-reserved names can never be claimed by a
    # human sign-in.
    _RESERVED = frozenset({"cheese", "zhishi", "system", "admin"})

    async def login(self, handle: str, name: str = "") -> User:
        """极简登录 (Phase 0): get-or-create, no password — handle IS the
        identity. A known handle signs in; a new one registers on the spot."""
        if handle in self._RESERVED:
            raise ValidationError(f"'{handle}' 是保留名，换一个吧")
        user = await self._repo.get_by_handle(handle)
        if user is not None:
            return user
        return await self._repo.add(User(handle=handle, name=name or handle))

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
