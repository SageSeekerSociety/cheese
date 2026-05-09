from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.space.models import (
    Space,
    SpaceAdminRelation,
    SpaceAdminRole,
    SpaceCategory,
    SpaceClassificationTopicsRelation,
    SpaceUserRank,
)
from app.domain.topics.models import Topic


class SpaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, space_id: int) -> Space | None:
        stmt: Select[tuple[Space]] = select(Space).where(
            and_(Space.id == space_id, Space.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_spaces(self, *, limit: int, offset: int = 0) -> Sequence[Space]:
        stmt: Select[tuple[Space]] = select(Space).where(Space.deleted_at.is_(None))
        stmt = stmt.order_by(Space.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_spaces(self) -> int:
        stmt = select(func.count(Space.id)).where(Space.deleted_at.is_(None))
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def exists_by_name(self, name: str) -> bool:
        stmt = select(Space.id).where(and_(Space.name == name, Space.deleted_at.is_(None)))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def create_space(
        self,
        *,
        name: str,
        intro: str,
        description: str,
        avatar_id: int | None,
        enable_rank: bool,
        announcements: list,
        task_templates: list,
    ) -> Space:
        now = datetime.now(UTC).replace(tzinfo=None)
        space = Space(
            name=name,
            intro=intro,
            description=description,
            avatar_id=avatar_id,
            enable_rank=enable_rank,
            announcements=announcements,
            task_templates=task_templates,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(space)
        await self._session.flush()
        return space

    async def save(self, space: Space) -> Space:
        self._session.add(space)
        await self._session.flush()
        return space


class SpaceCategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id_and_space(
        self,
        category_id: int,
        space_id: int,
    ) -> SpaceCategory | None:
        """Return category by id and space id (including archived ones, excluding deleted)."""
        stmt: Select[tuple[SpaceCategory]] = select(SpaceCategory).where(
            SpaceCategory.id == category_id,
            SpaceCategory.space_id == space_id,
            SpaceCategory.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_categories_for_space(
        self, space_id: int, include_archived: bool = False
    ) -> Sequence[SpaceCategory]:
        stmt: Select[tuple[SpaceCategory]] = select(SpaceCategory).where(
            SpaceCategory.space_id == space_id,
            SpaceCategory.deleted_at.is_(None),
        )
        if not include_archived:
            stmt = stmt.where(SpaceCategory.archived_at.is_(None))
        stmt = stmt.order_by(SpaceCategory.display_order.asc(), SpaceCategory.name.asc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create_category(
        self,
        *,
        space_id: int,
        name: str,
        description: str | None,
        display_order: int,
    ) -> SpaceCategory:
        now = datetime.now(UTC).replace(tzinfo=None)
        category = SpaceCategory(
            space_id=space_id,
            name=name,
            description=description,
            display_order=display_order,
            archived_at=None,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(category)
        await self._session.flush()
        return category

    async def save(self, category: SpaceCategory) -> SpaceCategory:
        self._session.add(category)
        await self._session.flush()
        return category

    async def exists_unarchived_name(self, space_id: int, name: str) -> bool:
        stmt = select(SpaceCategory.id).where(
            SpaceCategory.space_id == space_id,
            SpaceCategory.name == name,
            SpaceCategory.deleted_at.is_(None),
            SpaceCategory.archived_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_by_id(self, category_id: int) -> SpaceCategory | None:
        stmt: Select[tuple[SpaceCategory]] = select(SpaceCategory).where(
            SpaceCategory.id == category_id,
            SpaceCategory.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


class SpaceUserRankRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_rank(self, space_id: int, user_id: int) -> int:
        stmt: Select[tuple[SpaceUserRank]] = select(SpaceUserRank).where(
            SpaceUserRank.space_id == space_id,
            SpaceUserRank.user_id == user_id,
            SpaceUserRank.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None or row.rank is None:
            return 0
        return int(row.rank)

    async def increment_rank(self, *, space_id: int, user_id: int, delta: int) -> int:
        stmt: Select[tuple[SpaceUserRank]] = select(SpaceUserRank).where(
            SpaceUserRank.space_id == space_id,
            SpaceUserRank.user_id == user_id,
            SpaceUserRank.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        now = datetime.now(UTC).replace(tzinfo=None)
        if row is None:
            row = SpaceUserRank(
                space_id=space_id,
                user_id=user_id,
                rank=max(0, delta),
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            self._session.add(row)
        else:
            row.rank = max(0, row.rank + delta)
            row.updated_at = now
        await self._session.flush()
        return row.rank

    async def set_rank(self, *, space_id: int, user_id: int, rank: int) -> int:
        stmt: Select[tuple[SpaceUserRank]] = select(SpaceUserRank).where(
            SpaceUserRank.space_id == space_id,
            SpaceUserRank.user_id == user_id,
            SpaceUserRank.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        now = datetime.now(UTC).replace(tzinfo=None)
        if row is None:
            row = SpaceUserRank(
                space_id=space_id,
                user_id=user_id,
                rank=max(0, rank),
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            self._session.add(row)
        else:
            row.rank = max(0, rank)
            row.updated_at = now
        await self._session.flush()
        return row.rank


class SpaceAdminRelationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_admin(
        self, *, space_id: int, user_id: int, role: SpaceAdminRole
    ) -> SpaceAdminRelation:
        now = datetime.now(UTC).replace(tzinfo=None)
        relation = SpaceAdminRelation(
            space_id=space_id,
            user_id=user_id,
            role=role.value,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(relation)
        await self._session.flush()
        return relation

    async def get_relation(self, space_id: int, user_id: int) -> SpaceAdminRelation | None:
        stmt: Select[tuple[SpaceAdminRelation]] = select(SpaceAdminRelation).where(
            SpaceAdminRelation.space_id == space_id,
            SpaceAdminRelation.user_id == user_id,
            SpaceAdminRelation.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_admins(self, space_id: int) -> Sequence[SpaceAdminRelation]:
        stmt: Select[tuple[SpaceAdminRelation]] = (
            select(SpaceAdminRelation)
            .where(
                SpaceAdminRelation.space_id == space_id,
                SpaceAdminRelation.deleted_at.is_(None),
            )
            .order_by(SpaceAdminRelation.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def remove_admin(self, relation: SpaceAdminRelation) -> None:
        relation.deleted_at = relation.updated_at = datetime.now(UTC).replace(tzinfo=None)
        await self._session.flush()

    async def get_owner(self, space_id: int) -> SpaceAdminRelation | None:
        stmt: Select[tuple[SpaceAdminRelation]] = select(SpaceAdminRelation).where(
            SpaceAdminRelation.space_id == space_id,
            SpaceAdminRelation.role == SpaceAdminRole.OWNER.value,
            SpaceAdminRelation.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def save(self, relation: SpaceAdminRelation) -> SpaceAdminRelation:
        self._session.add(relation)
        await self._session.flush()
        return relation


class SpaceClassificationTopicsRepository:
    """Persists the Space ↔ Topic links exposed as `space.classificationTopics`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_topics_for_space(self, space_id: int) -> list[Topic]:
        stmt = (
            select(Topic)
            .join(
                SpaceClassificationTopicsRelation,
                SpaceClassificationTopicsRelation.topic_id == Topic.id,
            )
            .where(
                SpaceClassificationTopicsRelation.space_id == space_id,
                SpaceClassificationTopicsRelation.deleted_at.is_(None),
                Topic.deleted_at.is_(None),
            )
            .order_by(Topic.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_topics_for_spaces(
        self, space_ids: Sequence[int]
    ) -> dict[int, list[Topic]]:
        """Bulk variant. Returns {space_id: [Topic, ...]}."""
        if not space_ids:
            return {}
        stmt = (
            select(SpaceClassificationTopicsRelation.space_id, Topic)
            .join(Topic, Topic.id == SpaceClassificationTopicsRelation.topic_id)
            .where(
                SpaceClassificationTopicsRelation.space_id.in_(list(space_ids)),
                SpaceClassificationTopicsRelation.deleted_at.is_(None),
                Topic.deleted_at.is_(None),
            )
            .order_by(Topic.id.asc())
        )
        result = await self._session.execute(stmt)
        mapping: dict[int, list[Topic]] = {}
        for sid, topic in result.all():
            mapping.setdefault(sid, []).append(topic)
        return mapping

    async def replace_topics_for_space(
        self, *, space_id: int, topic_ids: Sequence[int]
    ) -> None:
        """Soft-delete existing links then insert the given ones in order."""
        now = datetime.now(UTC).replace(tzinfo=None)
        existing_stmt: Select[tuple[SpaceClassificationTopicsRelation]] = select(
            SpaceClassificationTopicsRelation
        ).where(
            SpaceClassificationTopicsRelation.space_id == space_id,
            SpaceClassificationTopicsRelation.deleted_at.is_(None),
        )
        existing = (await self._session.execute(existing_stmt)).scalars().all()
        for relation in existing:
            relation.deleted_at = now
            relation.updated_at = now
        for topic_id in topic_ids:
            self._session.add(
                SpaceClassificationTopicsRelation(
                    space_id=space_id,
                    topic_id=topic_id,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
            )
        await self._session.flush()
