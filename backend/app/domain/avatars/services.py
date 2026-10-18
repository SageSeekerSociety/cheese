from app.core.errors import NotFoundError
from app.domain.avatars.models import Avatar
from app.domain.avatars.repositories import AvatarRepository


class AvatarService:
    def __init__(self, repo: AvatarRepository) -> None:
        self._repo = repo

    async def list_predefined_ids(self) -> list[int]:
        avatars = await self._repo.list_by_type("predefined")
        return [a.id for a in avatars]

    async def get_avatar_raw(self, avatar_id: int) -> Avatar | None:
        return await self._repo.get_by_id(avatar_id)

    async def get_default_raw(self) -> Avatar | None:
        return await self._repo.get_default()

    async def get_default_id(self) -> int:
        avatar = await self._repo.get_default()
        if avatar is None:
            raise NotFoundError("No default avatar found")
        return avatar.id

    async def create_avatar(
        self, *, url: str, name: str, avatar_type: str = "UPLOADED"
    ) -> dict:
        avatar = await self._repo.create(url=url, name=name, avatar_type=avatar_type)
        return {"avatarId": avatar.id}
