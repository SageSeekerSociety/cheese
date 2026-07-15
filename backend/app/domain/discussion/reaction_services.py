from app.core.errors import NotFoundError
from app.domain.discussion.models import ReactionType
from app.domain.discussion.repositories import (
    DiscussionReactionRepository,
    ReactionTypeRepository,
)


class DiscussionReactionService:
    def __init__(
        self,
        reaction_repo: DiscussionReactionRepository,
        reaction_type_repo: ReactionTypeRepository,
    ) -> None:
        self._reaction_repo = reaction_repo
        self._reaction_type_repo = reaction_type_repo

    async def ensure_default_reaction_types(self) -> None:
        await self._reaction_type_repo.ensure_defaults()

    async def toggle(
        self,
        *,
        discussion_id: int,
        user_id: int,
        reaction_type_id: int,
    ) -> dict:
        await self.ensure_default_reaction_types()
        reaction_type = await self._reaction_type_repo.get_by_id(reaction_type_id)
        if reaction_type is None:
            raise NotFoundError(
                "reaction type not found", data={"id": reaction_type_id}
            )
        await self._reaction_repo.toggle(
            discussion_id=discussion_id,
            user_id=user_id,
            reaction_type_id=reaction_type_id,
        )
        counts = await self._reaction_repo.count_by_discussion(discussion_id)
        has_reacted = await self._reaction_repo.has_user_reacted(
            discussion_id=discussion_id,
            user_id=user_id,
            reaction_type_id=reaction_type_id,
        )
        return {
            "reactionType": self._reaction_type_to_dict(reaction_type),
            "count": counts.get(reaction_type_id, 0),
            "hasReacted": has_reacted,
        }

    async def remove(
        self,
        *,
        discussion_id: int,
        user_id: int,
        reaction_type_id: int,
    ) -> dict:
        await self.ensure_default_reaction_types()
        reaction_type = await self._reaction_type_repo.get_by_id(reaction_type_id)
        if reaction_type is None:
            raise NotFoundError(
                "reaction type not found", data={"id": reaction_type_id}
            )
        await self._reaction_repo.remove(
            discussion_id=discussion_id,
            user_id=user_id,
            reaction_type_id=reaction_type_id,
        )
        counts = await self._reaction_repo.count_by_discussion(discussion_id)
        has_reacted = await self._reaction_repo.has_user_reacted(
            discussion_id=discussion_id,
            user_id=user_id,
            reaction_type_id=reaction_type_id,
        )
        return {
            "reactionType": self._reaction_type_to_dict(reaction_type),
            "count": counts.get(reaction_type_id, 0),
            "hasReacted": has_reacted,
        }

    async def get_reaction_summary(
        self,
        *,
        discussion_id: int,
        current_user_id: int | None,
    ) -> list[dict]:
        await self.ensure_default_reaction_types()
        counts = await self._reaction_repo.count_by_discussion(discussion_id)
        reaction_types = await self._reaction_type_repo.list_active()
        summaries: list[dict] = []
        for rt in reaction_types:
            total = counts.get(rt.id, 0)
            active = False
            if current_user_id is not None and total:
                active = await self._reaction_repo.has_user_reacted(
                    discussion_id=discussion_id,
                    user_id=current_user_id,
                    reaction_type_id=rt.id,
                )
            summaries.append(
                {
                    "reactionType": self._reaction_type_to_dict(rt),
                    "count": total,
                    "hasReacted": active,
                }
            )
        return summaries

    async def list_reaction_types(self) -> list[dict]:
        await self.ensure_default_reaction_types()
        types = await self._reaction_type_repo.list_active()
        return [self._reaction_type_to_dict(rt) for rt in types]

    @staticmethod
    def _reaction_type_to_dict(rt: ReactionType) -> dict:
        return {
            "id": rt.id,
            "code": rt.code,
            "name": rt.name,
            "description": rt.description,
            "displayOrder": rt.display_order,
            "isActive": rt.is_active,
        }
