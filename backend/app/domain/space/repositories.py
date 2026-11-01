from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import ColumnExpressionArgument, Select, and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.request_facts import recall, remember

# The 访问域组 pair and the membership pair moved to modules of their own so
# this one keeps a single reason to change. Both are re-exported here because
# this module is the space domain's repository facade: every caller, and the
# three unit-test modules, reach them as
# ``from app.domain.space.repositories import ...``.
from app.domain.space.group_repositories import (
    SpaceDomainGroupDomainRepository,
    SpaceDomainGroupRepository,
)
from app.domain.space.member_repositories import (
    SpaceInviteCodeRepository,
    SpaceMemberRepository,
)
from app.domain.space.models import (
    Space,
    SpaceAdminRelation,
    SpaceAdminRole,
    SpaceCategory,
    SpaceClassificationTagRelation,
    SpaceMember,
    SpaceUserRank,
)
from app.domain.tag.models import Tag

__all__ = [
    "SpaceAdminRelationRepository",
    "SpaceCategoryRepository",
    "SpaceClassificationTopicsRepository",
    "SpaceDomainGroupDomainRepository",
    "SpaceDomainGroupRepository",
    "SpaceInviteCodeRepository",
    "SpaceMemberRepository",
    "SpaceRepository",
    "SpaceUserRankRepository",
]


class SpaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, space_id: int) -> Space | None:
        """The space, None when there is none or it was deleted. A problem's
        page asks it from its permission check and again from its payload, so
        a request reads the row once."""
        key = ("space_row", space_id)
        found, cached = recall(self._session, key)
        if found:
            return cached
        stmt: Select[tuple[Space]] = select(Space).where(
            and_(Space.id == space_id, Space.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        space = result.scalar_one_or_none()
        remember(self._session, key, space, tables=frozenset({"space"}))
        return space

    async def is_member(self, *, space_id: int, user_id: int) -> bool:
        """The single-space form of ``build_membership_predicate``.

        A direct link and the list ask one question, so a board cannot be
        hidden from the list and still answer at its own address.
        """
        stmt = select(Space.id).where(
            Space.id == space_id,
            Space.deleted_at.is_(None),
            self.build_membership_predicate(user_id=user_id),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def list_spaces(
        self, *, limit: int, offset: int = 0, member_user_id: int
    ) -> Sequence[Space]:
        """The 题目版 this user is in — created, administered, or joined.

        There is deliberately no unfiltered variant: a caller that forgets the
        viewer would hand back a board nobody invited them to, and the list
        page is the easiest place to never notice. A 题目版 still under review
        is in nobody's list — approval gates everyone, the creator included,
        who watches it through ``/space-applications`` instead.
        """
        stmt: Select[tuple[Space]] = select(Space).where(
            Space.deleted_at.is_(None),
            Space.review_status == "APPROVED",
            SpaceRepository.build_membership_predicate(user_id=member_user_id),
        )
        stmt = stmt.order_by(Space.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_spaces(self, *, member_user_id: int) -> int:
        stmt = select(func.count(Space.id)).where(
            Space.deleted_at.is_(None),
            Space.review_status == "APPROVED",
            SpaceRepository.build_membership_predicate(user_id=member_user_id),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def names(self, *, space_ids: Sequence[int]) -> dict[int, str]:
        """Each board's name, deleted boards included.

        For records of what happened on a board: a board removed since then is
        still where it happened.
        """
        if not space_ids:
            return {}
        stmt = select(Space.id, Space.name).where(Space.id.in_(space_ids))
        result = await self._session.execute(stmt)
        return {int(row[0]): row[1] for row in result.all()}

    @staticmethod
    def build_membership_predicate(*, user_id: int) -> ColumnElement[bool]:
        """「这个题目版是不是他的」, as a SQL predicate over Space.

        The one rule, in one place: a list query filters by it and a direct
        link is refused by it, so hiding a board and refusing to read it are
        the same answer rather than two that agree today.

        Both branches are needed and neither is redundant. The member row is
        what joining writes; the admin relation is how a board's creator and
        its managers see their own board without anyone having had to write
        one — which is also every board that predates membership existing.
        """
        member_exists = exists().where(
            SpaceMember.space_id == Space.id,
            SpaceMember.user_id == user_id,
            SpaceMember.deleted_at.is_(None),
        )
        admin_exists = exists().where(
            SpaceAdminRelation.space_id == Space.id,
            SpaceAdminRelation.user_id == user_id,
            SpaceAdminRelation.deleted_at.is_(None),
        )
        return or_(member_exists, admin_exists)

    @staticmethod
    def build_member_of_predicate(
        *, space_id: ColumnExpressionArgument[int], user_id: int
    ) -> ColumnElement[bool]:
        """「他在 ``space_id`` 这块板里」, as a SQL predicate over whatever row
        carries that column — ``is_member`` for a query over another table, such
        as a board's 题目 asking whether the reader is in their board."""
        return exists().where(
            Space.id == space_id,
            Space.deleted_at.is_(None),
            SpaceRepository.build_membership_predicate(user_id=user_id),
        )

    async def exists_by_name(self, name: str) -> bool:
        stmt = select(Space.id).where(
            and_(Space.name == name, Space.deleted_at.is_(None))
        )
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
        task_templates: list,
        visible_task_limit: int | None = None,
    ) -> Space:
        now = datetime.now(UTC)
        space = Space(
            name=name,
            intro=intro,
            description=description,
            avatar_id=avatar_id,
            enable_rank=enable_rank,
            visible_task_limit=visible_task_limit,
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
        stmt = stmt.order_by(
            SpaceCategory.display_order.asc(), SpaceCategory.name.asc()
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create_category(
        self,
        *,
        space_id: int,
        name: str,
        description: str | None,
        display_order: int,
        default_role: str | None = None,
    ) -> SpaceCategory:
        now = datetime.now(UTC)
        category = SpaceCategory(
            space_id=space_id,
            name=name,
            description=description,
            display_order=display_order,
            default_role=default_role,
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
        now = datetime.now(UTC)
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
        now = datetime.now(UTC)
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
        now = datetime.now(UTC)
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

    async def get_relation(
        self, space_id: int, user_id: int
    ) -> SpaceAdminRelation | None:
        """The person's owner or admin row in the space. The problem's
        visibility and the space's seat both ask it in one request; it is read
        once."""
        key = ("space_admin_relation", space_id, user_id)
        found, cached = recall(self._session, key)
        if found:
            return cached
        stmt: Select[tuple[SpaceAdminRelation]] = select(SpaceAdminRelation).where(
            SpaceAdminRelation.space_id == space_id,
            SpaceAdminRelation.user_id == user_id,
            SpaceAdminRelation.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        relation = result.scalar_one_or_none()
        remember(
            self._session, key, relation, tables=frozenset({"space_admin_relation"})
        )
        return relation

    async def administered(self, user_id: int, space_ids: Sequence[int]) -> set[int]:
        """Which of these spaces ``user_id`` is an owner or admin of."""
        if not space_ids:
            return set()
        stmt = select(SpaceAdminRelation.space_id).where(
            SpaceAdminRelation.user_id == user_id,
            SpaceAdminRelation.space_id.in_(list(space_ids)),
            SpaceAdminRelation.deleted_at.is_(None),
        )
        return {int(i) for i in (await self._session.execute(stmt)).scalars()}

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
        relation.deleted_at = relation.updated_at = datetime.now(UTC)
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

    async def list_topics_for_space(self, space_id: int) -> list[Tag]:
        stmt = (
            select(Tag)
            .join(
                SpaceClassificationTagRelation,
                SpaceClassificationTagRelation.tag_id == Tag.id,
            )
            .where(
                SpaceClassificationTagRelation.space_id == space_id,
                SpaceClassificationTagRelation.deleted_at.is_(None),
                Tag.deleted_at.is_(None),
            )
            .order_by(Tag.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_topics_for_spaces(
        self, space_ids: Sequence[int]
    ) -> dict[int, list[Tag]]:
        """Bulk variant. Returns {space_id: [Topic, ...]}."""
        if not space_ids:
            return {}
        stmt = (
            select(SpaceClassificationTagRelation.space_id, Tag)
            .join(Tag, Tag.id == SpaceClassificationTagRelation.tag_id)
            .where(
                SpaceClassificationTagRelation.space_id.in_(list(space_ids)),
                SpaceClassificationTagRelation.deleted_at.is_(None),
                Tag.deleted_at.is_(None),
            )
            .order_by(Tag.id.asc())
        )
        result = await self._session.execute(stmt)
        mapping: dict[int, list[Tag]] = {}
        for sid, topic in result.all():
            mapping.setdefault(sid, []).append(topic)
        return mapping

    async def replace_topics_for_space(
        self, *, space_id: int, topic_ids: Sequence[int]
    ) -> None:
        """Soft-delete existing links then insert the given ones in order."""
        now = datetime.now(UTC)
        existing_stmt: Select[tuple[SpaceClassificationTagRelation]] = select(
            SpaceClassificationTagRelation
        ).where(
            SpaceClassificationTagRelation.space_id == space_id,
            SpaceClassificationTagRelation.deleted_at.is_(None),
        )
        existing = (await self._session.execute(existing_stmt)).scalars().all()
        for relation in existing:
            relation.deleted_at = now
            relation.updated_at = now
        for topic_id in topic_ids:
            self._session.add(
                SpaceClassificationTagRelation(
                    space_id=space_id,
                    tag_id=topic_id,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
            )
        await self._session.flush()
