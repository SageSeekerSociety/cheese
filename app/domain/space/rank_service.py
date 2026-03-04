from __future__ import annotations

from app.domain.space.repositories import SpaceRepository, SpaceUserRankRepository


class SpaceRankService:
    def __init__(
        self,
        space_repo: SpaceRepository | None,
        rank_repo: SpaceUserRankRepository | None,
    ) -> None:
        self._space_repo = space_repo
        self._rank_repo = rank_repo

    async def award_rank(
        self, *, space_id: int | None, user_id: int | None, delta: int = 1
    ) -> bool:
        if (
            space_id is None
            or user_id is None
            or self._space_repo is None
            or self._rank_repo is None
            or delta <= 0
        ):
            return False
        space = await self._space_repo.get_by_id(space_id)
        if space is None or not space.enable_rank:
            return False
        await self._rank_repo.increment_rank(space_id=space_id, user_id=user_id, delta=delta)
        return True

    async def award_rank_if_higher(
        self,
        *,
        space_id: int | None,
        user_id: int | None,
        task_rank: int | None,
    ) -> bool:
        if (
            space_id is None
            or user_id is None
            or task_rank is None
            or self._space_repo is None
            or self._rank_repo is None
        ):
            return False
        space = await self._space_repo.get_by_id(space_id)
        if space is None or not space.enable_rank:
            return False
        current_rank = await self._rank_repo.get_rank(space_id=space_id, user_id=user_id)
        if current_rank >= task_rank:
            return False
        await self._rank_repo.set_rank(space_id=space_id, user_id=user_id, rank=task_rank)
        return True
