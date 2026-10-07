from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

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

    @classmethod
    def for_lookup(cls, session: AsyncSession) -> "KnowledgeService":
        """A reader another domain can build without our DI graph.

        The import guard keeps other domains out of our repository, and the
        constructors they would have to copy need the team and user
        repositories. This hands them only what a plain fetch needs.
        """
        return cls(repo=KnowledgeRepository(session), team_repo=TeamRepository(session))

    async def get_many(self, knowledge_ids: Sequence[int]) -> list[Knowledge]:
        """Whatever of these ids is still readable, in the order asked for.

        No membership check: the caller is a 课程 resolving the ids its teacher
        configured, and a teacher can only point at entries they could already
        see. That premise is what `ensure_readable` makes true on the write
        side, so it is checked once, when the config is saved, not per turn.
        `content` comes back with the row — trimming it is the caller's call,
        because only the caller knows how big its own prompt may grow.
        """
        return await self._repo.get_by_ids(knowledge_ids)

    async def ensure_readable(
        self, *, knowledge_ids: Sequence[int], user_id: int
    ) -> None:
        """Raise unless every one of these ids exists and this user may read it.

        The write side's half of the contract `get_many` states but does not
        check: a 项目集's teaching config may only name 知识 its teacher could
        already read. The criterion is `get`'s — membership of the entry's
        team — so anybody who can open `GET /knowledge/{id}` can point at it
        and nobody else can. A missing id (404) is told apart from a foreign
        one (403) because the caller is a form that can name the bad field.
        """
        for knowledge_id in knowledge_ids:
            entity = await self._repo.get_by_id(knowledge_id)
            if entity is None:
                raise NotFoundError(
                    "Resource knowledge not found",
                    data={"type": "knowledge", "id": knowledge_id},
                )
            await self._ensure_team_member(entity.team_id, user_id)

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
                "Resource knowledge not found",
                data={"type": "knowledge", "id": knowledge_id},
            )
        await self._ensure_team_member(entity.team_id, user_id)
        return await self._build_dto(entity, current_user_id=user_id)

    async def delete(self, *, knowledge_id: int, user_id: int) -> None:
        entity = await self._repo.get_by_id(knowledge_id)
        if entity is None:
            raise NotFoundError(
                "Resource knowledge not found",
                data={"type": "knowledge", "id": knowledge_id},
            )
        await self._ensure_team_member(entity.team_id, user_id)
        deleted = await self._repo.soft_delete(knowledge_id)
        if not deleted:
            raise NotFoundError(
                "Resource knowledge not found",
                data={"type": "knowledge", "id": knowledge_id},
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
                "Resource knowledge not found",
                data={"type": "knowledge", "id": knowledge_id},
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
                "Resource knowledge not found",
                data={"type": "knowledge", "id": knowledge_id},
            )
        await self._ensure_team_member(entity.team_id, user_id)
        await self._repo.add_upvote(knowledge_id, user_id)
        return await self._build_dto(entity, current_user_id=user_id)

    async def remove_upvote(self, *, knowledge_id: int, user_id: int) -> dict:
        entity = await self._repo.get_by_id(knowledge_id)
        if entity is None:
            raise NotFoundError(
                "Resource knowledge not found",
                data={"type": "knowledge", "id": knowledge_id},
            )
        await self._ensure_team_member(entity.team_id, user_id)
        await self._repo.remove_upvote(knowledge_id, user_id)
        return await self._build_dto(entity, current_user_id=user_id)

    async def _build_dto(
        self, entity: Knowledge, *, current_user_id: int | None
    ) -> dict:
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
            is_upvoted = (
                entity.id in user_upvotes if current_user_id is not None else False
            )
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
        # 头像只给本人**自己挑过**的那张：``profile.avatar_id`` 是原始值，注册时人人被
        # 写上全局默认那一行，直接回它会让没挑过的人共用一张默认脸。判据只有一处
        # （``UserProfileRepository.chosen_avatar_ids``）：没挑过的人不在映射里，
        # 回 None，交给前端的 UserAvatar 画彩色首字母。
        chosen = await self._profile_repo.chosen_avatar_ids(cleaned)
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
                "avatarId": chosen.get(uid),
                "intro": profile.intro if profile else "",
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
        created_at = (
            int(entity.created_at.timestamp() * 1000) if entity.created_at else 0
        )
        updated_at = (
            int(entity.updated_at.timestamp() * 1000) if entity.updated_at else 0
        )
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
        "question_count": 0,
        "answer_count": 0,
    }
