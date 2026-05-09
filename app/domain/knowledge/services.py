from collections.abc import Sequence

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.knowledge.models import Knowledge
from app.domain.knowledge.repositories import KnowledgeRepository
from app.domain.team.repositories import TeamRepository
from app.domain.user.repositories import UserProfileRepository, UserRepository


class KnowledgeService:
    def __init__(
        self,
        repo: KnowledgeRepository,
        team_repo: TeamRepository,
        user_repo: UserRepository | None = None,
        profile_repo: UserProfileRepository | None = None,
    ) -> None:
        self._repo = repo
        self._team_repo = team_repo
        self._user_repo = user_repo
        self._profile_repo = profile_repo

    async def create(
        self,
        *,
        name: str,
        type_: str,
        content,
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

    async def find_all(
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
        rows, total = await self._repo.find_all(
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
        content=None,
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
        creator_map = await self._load_creators([entity.created_by])
        return self._to_dto(
            entity,
            label_map.get(entity.id, []),
            count,
            is_upvoted,
            creator_map.get(entity.created_by),
        )

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
        creator_ids = list({k.created_by for k in entities if k.created_by is not None})
        creator_map = await self._load_creators(creator_ids)
        result: list[dict] = []
        for entity in entities:
            count = count_map.get(entity.id, 0)
            is_upvoted = entity.id in user_upvotes if current_user_id is not None else False
            result.append(
                self._to_dto(
                    entity,
                    label_map.get(entity.id, []),
                    count,
                    is_upvoted,
                    creator_map.get(entity.created_by),
                )
            )
        return result

    async def _load_creators(self, user_ids: Sequence[int]) -> dict[int, dict]:
        cleaned = [uid for uid in user_ids if isinstance(uid, int) and uid > 0]
        if not cleaned or self._user_repo is None or self._profile_repo is None:
            # Fall back to id-only User stubs so frontend's `creator.id` paths
            # still work even when repos aren't wired in legacy tests.
            return {uid: _user_stub(uid) for uid in cleaned}
        users = await self._user_repo.get_by_ids(cleaned)
        profiles = await self._profile_repo.get_profiles_by_user_ids(cleaned)
        out: dict[int, dict] = {}
        for uid in cleaned:
            user = users.get(uid)
            profile = profiles.get(uid)
            if user is None:
                out[uid] = _user_stub(uid)
                continue
            nickname = (
                profile.nickname
                if profile and getattr(profile, "nickname", None)
                else user.username
            )
            out[uid] = {
                "id": user.id,
                "username": user.username,
                "nickname": nickname,
                "avatarId": profile.avatar_id if profile else None,
                "intro": profile.intro if profile else "",
                "follow_count": 0,
                "fans_count": 0,
                "question_count": 0,
                "answer_count": 0,
            }
        return out

    def _to_dto(
        self,
        entity: Knowledge,
        labels: list[str],
        upvote_count: int,
        is_upvoted: bool,
        creator: dict | None = None,
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
            # Frontend `Knowledge.creator: User` is required; keep
            # `createdBy` for backwards-compat with any internal callers.
            "creator": creator or _user_stub(entity.created_by),
            "createdBy": entity.created_by,
            "createdAt": created_at,
            "updatedAt": updated_at,
            "upvoteCount": upvote_count,
            "isUpvoted": is_upvoted,
        }

    async def _ensure_team_member(self, team_id: int, user_id: int) -> None:
        if not await self._team_repo.is_team_member(team_id, user_id):
            raise ForbiddenError("User is not a member of the team")


def _user_stub(user_id: int | None) -> dict:
    """Minimal User-shaped placeholder for unknown / deleted accounts."""
    return {
        "id": user_id or 0,
        "username": "",
        "nickname": "",
        "avatarId": None,
        "intro": "",
        "follow_count": 0,
        "fans_count": 0,
        "question_count": 0,
        "answer_count": 0,
    }
