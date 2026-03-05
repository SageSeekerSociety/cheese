from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, and_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.space.models import (
    Space,
    SpaceCategory,
    SpaceUserRank,
    SpaceAdminRelation,
    SpaceAdminRole,
)


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
        from datetime import datetime as _dt

        now = _dt.utcnow()
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
        from datetime import datetime as _dt

        now = _dt.utcnow()
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
        from datetime import datetime as _dt

        stmt: Select[tuple[SpaceUserRank]] = select(SpaceUserRank).where(
            SpaceUserRank.space_id == space_id,
            SpaceUserRank.user_id == user_id,
            SpaceUserRank.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        now = _dt.utcnow()
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
        from datetime import datetime as _dt

        stmt: Select[tuple[SpaceUserRank]] = select(SpaceUserRank).where(
            SpaceUserRank.space_id == space_id,
            SpaceUserRank.user_id == user_id,
            SpaceUserRank.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        now = _dt.utcnow()
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
        from datetime import datetime as _dt

        now = _dt.utcnow()
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
        relation.deleted_at = relation.updated_at = datetime.utcnow()
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
