from __future__ import annotations

from collections.abc import Sequence

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.knowledge.models import Knowledge
from app.domain.knowledge.repositories import KnowledgeRepository
from app.domain.team.repositories import TeamRepository


class KnowledgeService:
    def __init__(
        self,
        repo: KnowledgeRepository,
        team_repo: TeamRepository,
    ) -> None:
        self._repo = repo
        self._team_repo = team_repo

    async def create(
        self,
        *,
        name: str,
        type_: str,
        content: dict,
        description: str | None,
        team_id: int,
        created_by: int,
        labels: list[str],
        material_id: int | None,
        project_id: int | None,
        discussion_id: int | None,
    ) -> dict:
        await self._ensure_team_member(team_id, created_by)
        entity = await self._repo.create(
            name=name,
            type_=type_,
            content=content,
            description=description,
            team_id=team_id,
            material_id=material_id,
            project_id=project_id,
            discussion_id=discussion_id,
            created_by=created_by,
            labels=labels,
        )
        dto = await self._build_dto(entity, current_user_id=created_by)
        return dto

    async def list(
        self,
        *,
        team_id: int,
        user_id: int,
        project_id: int | None,
        type_: str | None,
        labels: list[str] | None,
        query: str | None,
        limit: int,
        offset: int,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[dict], int]:
        await self._ensure_team_member(team_id, user_id)
        rows, total = await self._repo.list(
            team_id=team_id,
            project_id=project_id,
            type_=type_,
            labels=labels,
            query=query,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        dtos = await self._build_dtos(rows, current_user_id=user_id)
        return dtos, total

    async def get(
        self,
        *,
        knowledge_id: int,
        user_id: int,
    ) -> dict:
        entity = await self._repo.get_by_id(knowledge_id)
        if entity is None:
            raise NotFoundError(
                "Resource knowledge not found", data={"type": "knowledge", "id": knowledge_id}
            )
        await self._ensure_team_member(entity.team_id, user_id)
        return await self._build_dto(entity, current_user_id=user_id)

    async def delete(self, *, knowledge_id: int, user_id: int) -> None:
        entity = await self._repo.get_by_id(knowledge_id)
        if entity is None:
            raise NotFoundError(
                "Resource knowledge not found", data={"type": "knowledge", "id": knowledge_id}
            )
        await self._ensure_team_member(entity.team_id, user_id)
        deleted = await self._repo.soft_delete(knowledge_id)
        if not deleted:
            raise NotFoundError(
                "Resource knowledge not found", data={"type": "knowledge", "id": knowledge_id}
            )

    async def update(
        self,
        *,
        knowledge_id: int,
        user_id: int,
        name: str | None = None,
        description: str | None = None,
        content: dict | None = None,
        labels: list[str] | None = None,
    ) -> dict:
        entity = await self._repo.get_by_id(knowledge_id)
        if entity is None:
            raise NotFoundError(
                "Resource knowledge not found", data={"type": "knowledge", "id": knowledge_id}
            )
        await self._ensure_team_member(entity.team_id, user_id)
        updated = await self._repo.update_entity(
            entity=entity,
            name=name,
            description=description,
            content=content,
        )
        if labels is not None:
            await self._repo.update_labels(knowledge_id, labels)
        return await self._build_dto(updated, current_user_id=user_id)

    async def upvote(self, *, knowledge_id: int, user_id: int) -> dict:
        entity = await self._repo.get_by_id(knowledge_id)
        if entity is None:
            raise NotFoundError(
                "Resource knowledge not found", data={"type": "knowledge", "id": knowledge_id}
            )
        await self._ensure_team_member(entity.team_id, user_id)
        await self._repo.add_upvote(knowledge_id, user_id)
        return await self._build_dto(entity, current_user_id=user_id)

    async def remove_upvote(self, *, knowledge_id: int, user_id: int) -> dict:
        entity = await self._repo.get_by_id(knowledge_id)
        if entity is None:
            raise NotFoundError(
                "Resource knowledge not found", data={"type": "knowledge", "id": knowledge_id}
            )
        await self._ensure_team_member(entity.team_id, user_id)
        await self._repo.remove_upvote(knowledge_id, user_id)
        return await self._build_dto(entity, current_user_id=user_id)

    async def _build_dto(self, entity: Knowledge, *, current_user_id: int | None) -> dict:
        label_map = await self._repo.get_labels_map([entity.id])
        count = await self._repo.get_upvote_count(entity.id)
        is_upvoted = False
        if current_user_id is not None:
            is_upvoted = await self._repo.has_upvote(entity.id, current_user_id)
        return self._to_dto(entity, label_map.get(entity.id, []), count, is_upvoted)

    async def _build_dtos(
        self,
        entities: Sequence[Knowledge],
        *,
        current_user_id: int | None,
    ) -> list[dict]:
        ids = [k.id for k in entities if k.id is not None]
        label_map = await self._repo.get_labels_map(ids)
        count_map = await self._repo.get_upvote_counts(ids)
        user_upvotes: set[int] = set()
        if current_user_id is not None:
            user_upvotes = await self._repo.list_user_upvotes(ids, current_user_id)
        result: list[dict] = []
        for entity in entities:
            count = count_map.get(entity.id, 0)
            is_upvoted = entity.id in user_upvotes if current_user_id is not None else False
            result.append(self._to_dto(entity, label_map.get(entity.id, []), count, is_upvoted))
        return result

    def _to_dto(
        self,
        entity: Knowledge,
        labels: list[str],
        upvote_count: int,
        is_upvoted: bool,
    ) -> dict:
        created_at = int(entity.created_at.timestamp() * 1000) if entity.created_at else 0
        updated_at = int(entity.updated_at.timestamp() * 1000) if entity.updated_at else 0
        return {
            "id": entity.id,
            "name": entity.name,
            "type": entity.type,
            "content": entity.content,
            "description": entity.description,
            "teamId": entity.team_id,
            "projectId": entity.project_id,
            "discussionId": entity.discussion_id,
            "materialId": entity.material_id,
            "labels": labels,
            "createdBy": entity.created_by,
            "createdAt": created_at,
            "updatedAt": updated_at,
            "upvoteCount": upvote_count,
            "isUpvoted": is_upvoted,
        }

    async def _ensure_team_member(self, team_id: int, user_id: int) -> None:
        if not await self._team_repo.is_team_member(team_id, user_id):
            raise ForbiddenError("User is not a member of the team")
