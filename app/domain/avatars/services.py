from __future__ import annotations

from app.core.errors import NotFoundError
from app.domain.avatars.models import Avatar
from app.domain.avatars.repositories import AvatarRepository


def _avatar_to_dto(avatar: Avatar) -> dict:
    created_at_ms = int(avatar.created_at.timestamp() * 1000) if avatar.created_at else 0
    return {
        "id": avatar.id,
        "url": avatar.url,
        "name": avatar.name,
        "avatarType": avatar.avatar_type,
        "usageCount": avatar.usage_count,
        "createdAt": created_at_ms,
    }


class AvatarService:
    def __init__(self, repo: AvatarRepository) -> None:
        self._repo = repo

    async def list_predefined_ids(self) -> list[int]:
        avatars = await self._repo.list_by_type("predefined")
        return [a.id for a in avatars]

    async def get_avatar(self, avatar_id: int) -> dict:
        avatar = await self._repo.get_by_id(avatar_id)
        if avatar is None:
            raise NotFoundError("Avatar not found", data={"id": avatar_id})
        return _avatar_to_dto(avatar)

    async def get_avatar_raw(self, avatar_id: int) -> Avatar | None:
        return await self._repo.get_by_id(avatar_id)

    async def get_default(self) -> dict:
        avatar = await self._repo.get_default()
        if avatar is None:
            raise NotFoundError("No default avatar found")
        return _avatar_to_dto(avatar)

    async def get_default_raw(self) -> Avatar | None:
        return await self._repo.get_default()

    async def get_default_id(self) -> int:
        avatar = await self._repo.get_default()
        if avatar is None:
            raise NotFoundError("No default avatar found")
        return avatar.id

    async def create_avatar(self, *, url: str, name: str, avatar_type: str = "UPLOADED") -> dict:
        avatar = await self._repo.create(url=url, name=name, avatar_type=avatar_type)
        return {"avatarId": avatar.id}
