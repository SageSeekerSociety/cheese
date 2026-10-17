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
        summaries = await self.summaries_for(
            [discussion_id], current_user_id=current_user_id
        )
        return summaries[discussion_id]

    async def summaries_for(
        self,
        discussion_ids: list[int],
        *,
        current_user_id: int | None,
    ) -> dict[int, list[dict]]:
        if not discussion_ids:
            return {}
        await self.ensure_default_reaction_types()
        reactions = await self._reaction_repo.summaries_for(
            discussion_ids, current_user_id=current_user_id
        )
        reaction_types = await self._reaction_type_repo.list_active()
        summaries: dict[int, list[dict]] = {}
        for discussion_id in discussion_ids:
            counts = reactions.get(discussion_id, {})
            summaries[discussion_id] = []
            for rt in reaction_types:
                total, active = counts.get(rt.id, (0, False))
                summaries[discussion_id].append(
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
