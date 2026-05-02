from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.domain.discussion.models import DiscussableModelType
from app.domain.discussion.reaction_services import DiscussionReactionService
from app.domain.discussion.repositories import DiscussionRepository, _content_str_to_json
from app.domain.notification.models import NotificationType
from app.domain.notification.publisher import publish_notification_event
from app.domain.user.repositories import UserProfileRepository

import json


class DiscussionService:
    def __init__(
        self,
        repo: DiscussionRepository,
        reaction_service: DiscussionReactionService,
        profile_repo: UserProfileRepository,
        session: AsyncSession,
    ) -> None:
        self._repo = repo
        self._reaction_service = reaction_service
        self._profile_repo = profile_repo
        self._session = session

    async def create_discussion(
        self,
        *,
        user_id: int,
        content: str,
        model_type: str,
        model_id: int,
        parent_id: int | None = None,
        mentioned_user_ids: Sequence[int] | None = None,
    ) -> dict:
        if not content or not content.strip():
            raise BadRequestError("content is required")
        try:
            DiscussableModelType(model_type.upper())
        except ValueError as exc:
            raise BadRequestError(f"Invalid modelType: {model_type}") from exc

        entity = await self._repo.create(
            model_type=model_type.upper(),
            model_id=model_id,
            sender_id=user_id,
            content=content.strip(),
            parent_id=parent_id,
            mentioned_user_ids=list({uid for uid in (mentioned_user_ids or []) if uid > 0}),
        )

        if entity.mentioned_user_ids:
            await publish_notification_event(
                self._session,
                recipient_ids=set(entity.mentioned_user_ids),
                type_=NotificationType.MENTION,
                payload={
                    "discussion": {"type": "discussion", "id": str(entity.id)},
                    "model": {"type": entity.model_type, "id": str(entity.model_id)},
                    "excerpt": content.strip()[:120],
                },
                actor_id=user_id,
            )

        return await self._build_discussion_dto(entity, current_user_id=user_id)

    async def get_discussion(self, discussion_id: int, current_user_id: int | None) -> dict:
        entity = await self._repo.get_by_id(discussion_id)
        if entity is None:
            raise NotFoundError(
                "Resource discussion not found",
                data={"type": "discussion", "id": discussion_id},
            )
        return await self._build_discussion_dto(
            entity, current_user_id=current_user_id, include_subs=True
        )

    async def list_discussions(
        self,
        *,
        model_type: str | None,
        model_id: int | None,
        parent_id: int | None,
        page_start: int | None,
        page_size: int,
        sort_by: str,
        sort_order: str,
        current_user_id: int | None,
        include_subs: bool,
        with_reactions: bool,
    ) -> tuple[list[dict], dict]:
        offset = page_start or 0
        rows, total = await self._repo.find_all(
            model_type=model_type,
            model_id=model_id,
            parent_id=parent_id,
            limit=page_size,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        dtos = await self._build_discussion_dtos(
            rows,
            current_user_id=current_user_id,
            include_subs=include_subs,
            with_reactions=with_reactions,
        )
        returned = len(dtos)
        has_more = offset + returned < total
        next_start = offset + returned if has_more and returned > 0 else None
        page = {
            "pageStart": offset,
            "pageSize": returned,
            "hasMore": has_more,
            "nextStart": next_start,
            "total": total,
        }
        return dtos, page

    async def update_discussion(self, discussion_id: int, *, content: str, user_id: int) -> dict:
        entity = await self._repo.get_by_id(discussion_id)
        if entity is None:
            raise NotFoundError(
                "Resource discussion not found",
                data={"type": "discussion", "id": discussion_id},
            )
        from app.core.errors import ForbiddenError

        if entity.sender_id != user_id:
            raise ForbiddenError("Only the author can update this discussion")
        content_json = _content_str_to_json(content)
        updated = await self._repo.update_content(discussion_id, content_json)
        return await self._build_discussion_dto(updated, current_user_id=user_id)

    async def delete_discussion(self, discussion_id: int) -> None:
        deleted = await self._repo.soft_delete(discussion_id)
        if not deleted:
            raise NotFoundError(
                "Resource discussion not found",
                data={"type": "discussion", "id": discussion_id},
            )

    async def get_reaction_summary(self, discussion_id: int, user_id: int | None) -> list[dict]:
        return await self._reaction_service.get_reaction_summary(
            discussion_id=discussion_id,
            current_user_id=user_id,
        )

    async def list_reaction_types(self) -> list[dict]:
        return await self._reaction_service.list_reaction_types()

    async def toggle_reaction(
        self,
        *,
        discussion_id: int,
        reaction_type_id: int,
        user_id: int,
    ) -> dict:
        return await self._reaction_service.toggle(
            discussion_id=discussion_id,
            user_id=user_id,
            reaction_type_id=reaction_type_id,
        )

    async def remove_reaction(
        self,
        *,
        discussion_id: int,
        reaction_type_id: int,
        user_id: int,
    ) -> dict:
        return await self._reaction_service.remove(
            discussion_id=discussion_id,
            user_id=user_id,
            reaction_type_id=reaction_type_id,
        )

    async def _build_discussion_dtos(
        self,
        rows: Sequence,
        *,
        current_user_id: int | None,
        include_subs: bool,
        with_reactions: bool,
    ) -> list[dict]:
        if not rows:
            return []
        user_ids = {row.sender_id for row in rows}
        for row in rows:
            user_ids.update(row.mentioned_user_ids or [])
        user_map = await self._load_user_map(user_ids)
        dtos: list[dict] = []
        for row in rows:
            dtos.append(
                await self._build_discussion_dto(
                    row,
                    current_user_id=current_user_id,
                    user_map=user_map,
                    include_subs=include_subs,
                    with_reactions=with_reactions,
                )
            )
        return dtos

    async def _build_discussion_dto(
        self,
        entity,
        *,
        current_user_id: int | None,
        include_subs: bool = True,
        user_map: dict[int, dict] | None = None,
        with_reactions: bool = True,
    ) -> dict:
        sender = None
        if user_map is None:
            user_map = await self._load_user_map(
                {entity.sender_id, *(entity.mentioned_user_ids or [])}
            )
        sender = user_map.get(entity.sender_id)
        mentioned = [
            user_map.get(uid) for uid in entity.mentioned_user_ids or [] if user_map.get(uid)
        ]
        summary = (
            await self.get_reaction_summary(entity.id, current_user_id) if with_reactions else []
        )

        sub_info = None
        if include_subs:
            examples, _ = await self.list_discussions(
                model_type=None,
                model_id=None,
                parent_id=entity.id,
                page_start=0,
                page_size=2,
                sort_by="createdAt",
                sort_order="desc",
                current_user_id=current_user_id,
                include_subs=False,
                with_reactions=with_reactions,
            )
            count = await self._repo.count_children(entity.id)
            sub_info = {
                "count": count,
                "examples": examples,
            }

        created_at_ms = int(entity.created_at.timestamp() * 1000)
        updated_at_ms = int(entity.updated_at.timestamp() * 1000)

        content_value = entity.content
        if isinstance(content_value, (dict, list)):
            content_value = json.dumps(content_value, ensure_ascii=False, separators=(",", ":"))
        elif content_value is None:
            content_value = ""
        else:
            content_value = str(content_value)

        return {
            "id": entity.id,
            "modelType": entity.model_type,
            "modelId": entity.model_id,
            "content": content_value,
            "parentId": entity.parent_id,
            "sender": sender,
            "mentionedUsers": mentioned,
            "reactions": summary,
            "subDiscussions": sub_info,
            "createdAt": created_at_ms,
            "updatedAt": updated_at_ms,
        }

    async def _load_user_map(self, user_ids: set[int]) -> dict[int, dict]:
        user_map: dict[int, dict] = {}
        if not user_ids:
            return user_map
        profiles = await self._profile_repo.get_profiles_by_user_ids(list(user_ids))
        for uid in user_ids:
            profile = profiles.get(uid)
            if profile is None:
                continue
            user_map[uid] = {
                "id": uid,
                "nickname": profile.nickname,
                "avatarId": profile.avatar_id,
                "intro": profile.intro,
            }
        return user_map
