from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.space.models import (
    Space,
    SpaceAdminRelation,
    SpaceAdminRole,
    SpaceCategory,
    SpaceClassificationTagRelation,
    SpaceDomainGroup,
    SpaceDomainGroupDomain,
    SpaceInviteCode,
    SpaceMember,
    SpaceUserRank,
)
from app.domain.space.visibility_service import SpaceVisibilityService
from app.domain.tag.models import Tag


class SpaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, space_id: int) -> Space | None:
        stmt: Select[tuple[Space]] = select(Space).where(
            and_(Space.id == space_id, Space.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_spaces(
        self, *, limit: int, offset: int = 0, visible_to_user_id: int
    ) -> Sequence[Space]:
        """Spaces this user is allowed to see — see SpaceVisibilityService.

        There is deliberately no unfiltered variant: a caller that forgets
        the viewer would hand back another person's private space, and the
        list page is the easiest place to never notice.
        """
        stmt: Select[tuple[Space]] = select(Space).where(
            Space.deleted_at.is_(None),
            SpaceVisibilityService.build_visibility_predicate(
                user_id=visible_to_user_id
            ),
        )
        stmt = stmt.order_by(Space.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_spaces(self, *, visible_to_user_id: int) -> int:
        stmt = select(func.count(Space.id)).where(
            Space.deleted_at.is_(None),
            SpaceVisibilityService.build_visibility_predicate(
                user_id=visible_to_user_id
            ),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

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
        announcements: list,
        task_templates: list,
        visible_task_limit: int | None = None,
        visibility: int = 0,
    ) -> Space:
        now = datetime.now(UTC)
        space = Space(
            name=name,
            intro=intro,
            description=description,
            avatar_id=avatar_id,
            enable_rank=enable_rank,
            visible_task_limit=visible_task_limit,
            visibility=visibility,
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
        """Return category by id and space id (including archived ones, excluding deleted)."""  # noqa: E501
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
    ) -> SpaceCategory:
        now = datetime.now(UTC)
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


class SpaceDomainGroupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_groups(self, space_id: int) -> Sequence[SpaceDomainGroup]:
        stmt: Select[tuple[SpaceDomainGroup]] = (
            select(SpaceDomainGroup)
            .where(
                SpaceDomainGroup.space_id == space_id,
                SpaceDomainGroup.deleted_at.is_(None),
            )
            .order_by(SpaceDomainGroup.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(
        self, *, space_id: int, group_id: int
    ) -> SpaceDomainGroup | None:
        stmt: Select[tuple[SpaceDomainGroup]] = select(SpaceDomainGroup).where(
            SpaceDomainGroup.id == group_id,
            SpaceDomainGroup.space_id == space_id,
            SpaceDomainGroup.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def exists_name(self, *, space_id: int, name: str) -> bool:
        stmt = select(SpaceDomainGroup.id).where(
            SpaceDomainGroup.space_id == space_id,
            SpaceDomainGroup.name == name,
            SpaceDomainGroup.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def create_group(
        self, *, space_id: int, name: str, description: str | None
    ) -> SpaceDomainGroup:
        now = datetime.now(UTC)
        group = SpaceDomainGroup(
            space_id=space_id,
            name=name,
            description=description,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(group)
        await self._session.flush()
        return group

    async def save(self, group: SpaceDomainGroup) -> SpaceDomainGroup:
        self._session.add(group)
        await self._session.flush()
        return group


class SpaceDomainGroupDomainRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_domains_for_group(self, group_id: int) -> list[str]:
        stmt = (
            select(SpaceDomainGroupDomain.domain)
            .where(
                SpaceDomainGroupDomain.group_id == group_id,
                SpaceDomainGroupDomain.deleted_at.is_(None),
            )
            .order_by(SpaceDomainGroupDomain.domain.asc())
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def list_domains_for_groups(
        self, group_ids: Sequence[int]
    ) -> dict[int, list[str]]:
        if not group_ids:
            return {}
        stmt = (
            select(SpaceDomainGroupDomain.group_id, SpaceDomainGroupDomain.domain)
            .where(
                SpaceDomainGroupDomain.group_id.in_(list(group_ids)),
                SpaceDomainGroupDomain.deleted_at.is_(None),
            )
            .order_by(SpaceDomainGroupDomain.domain.asc())
        )
        result = await self._session.execute(stmt)
        mapping: dict[int, list[str]] = {}
        for group_id, domain in result.all():
            mapping.setdefault(int(group_id), []).append(domain)
        return mapping

    async def replace_domains(self, *, group_id: int, domains: Sequence[str]) -> None:
        now = datetime.now(UTC)
        stmt: Select[tuple[SpaceDomainGroupDomain]] = select(
            SpaceDomainGroupDomain
        ).where(
            SpaceDomainGroupDomain.group_id == group_id,
            SpaceDomainGroupDomain.deleted_at.is_(None),
        )
        existing = (await self._session.execute(stmt)).scalars().all()
        for item in existing:
            item.deleted_at = now
            item.updated_at = now
        for domain in domains:
            self._session.add(
                SpaceDomainGroupDomain(
                    group_id=group_id,
                    domain=domain,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
            )
        await self._session.flush()

    async def soft_delete_by_group(self, *, group_id: int) -> None:
        now = datetime.now(UTC)
        stmt: Select[tuple[SpaceDomainGroupDomain]] = select(
            SpaceDomainGroupDomain
        ).where(
            SpaceDomainGroupDomain.group_id == group_id,
            SpaceDomainGroupDomain.deleted_at.is_(None),
        )
        existing = (await self._session.execute(stmt)).scalars().all()
        for item in existing:
            item.deleted_at = now
            item.updated_at = now
        await self._session.flush()

    async def list_group_ids_by_domains(
        self, *, space_id: int, domains: Sequence[str]
    ) -> set[int]:
        """Return domain-group IDs whose stored domains intersect the given set."""
        if not domains:
            return set()
        stmt = (
            select(SpaceDomainGroupDomain.group_id)
            .join(
                SpaceDomainGroup, SpaceDomainGroupDomain.group_id == SpaceDomainGroup.id
            )
            .where(
                SpaceDomainGroup.space_id == space_id,
                SpaceDomainGroupDomain.domain.in_(list(domains)),
                SpaceDomainGroupDomain.deleted_at.is_(None),
                SpaceDomainGroup.deleted_at.is_(None),
            )
            .distinct()
        )
        result = await self._session.execute(stmt)
        return {int(row[0]) for row in result.all()}


class SpaceMemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_member(self, space_id: int, user_id: int) -> SpaceMember | None:
        stmt: Select[tuple[SpaceMember]] = select(SpaceMember).where(
            SpaceMember.space_id == space_id,
            SpaceMember.user_id == user_id,
            SpaceMember.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_members(self, space_id: int) -> Sequence[SpaceMember]:
        stmt: Select[tuple[SpaceMember]] = (
            select(SpaceMember)
            .where(
                SpaceMember.space_id == space_id,
                SpaceMember.deleted_at.is_(None),
            )
            .order_by(SpaceMember.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def add_member(self, *, space_id: int, user_id: int) -> SpaceMember:
        """Join, or re-join after having been removed.

        A removed member keeps a soft-deleted row, which is why this revives
        it instead of inserting a second one — otherwise the same person
        would accumulate a row per attempt and `list_members` would have to
        know to deduplicate.
        """
        now = datetime.now(UTC)
        member = await self._get_any_member(space_id, user_id)
        if member is not None:
            member.deleted_at = None
            member.updated_at = now
            self._session.add(member)
            await self._session.flush()
            return member

        member = SpaceMember(
            space_id=space_id,
            user_id=user_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(member)
        await self._session.flush()
        return member

    async def remove_member(self, member: SpaceMember) -> None:
        member.deleted_at = member.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def _get_any_member(self, space_id: int, user_id: int) -> SpaceMember | None:
        """Including soft-deleted rows — the caller decides what to do with one."""
        stmt: Select[tuple[SpaceMember]] = (
            select(SpaceMember)
            .where(
                SpaceMember.space_id == space_id,
                SpaceMember.user_id == user_id,
            )
            .order_by(SpaceMember.id.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


class SpaceInviteCodeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_code(
        self,
        *,
        space_id: int,
        code: str,
        max_uses: int,
        expires_at: datetime | None,
        created_by: int | None,
    ) -> SpaceInviteCode:
        now = datetime.now(UTC)
        invite = SpaceInviteCode(
            space_id=space_id,
            code=code,
            max_uses=max_uses,
            use_count=0,
            expires_at=expires_at,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(invite)
        await self._session.flush()
        return invite

    async def list_codes_for_space(self, space_id: int) -> Sequence[SpaceInviteCode]:
        stmt: Select[tuple[SpaceInviteCode]] = (
            select(SpaceInviteCode)
            .where(
                SpaceInviteCode.space_id == space_id,
                SpaceInviteCode.deleted_at.is_(None),
            )
            .order_by(SpaceInviteCode.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_code(self, code: str) -> SpaceInviteCode | None:
        stmt: Select[tuple[SpaceInviteCode]] = select(SpaceInviteCode).where(
            SpaceInviteCode.code == code,
            SpaceInviteCode.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def code_exists(self, code: str) -> bool:
        stmt = select(SpaceInviteCode.id).where(SpaceInviteCode.code == code)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def consume_use(self, code_id: int) -> bool:
        """Spend one use, only if there is one left.

        The limit is enforced inside the UPDATE rather than by reading the
        row first: two people redeeming the last use at the same moment both
        read "0 of 1" and both get in otherwise. Returns False when the
        UPDATE matched nothing, i.e. the code is exhausted.
        """
        stmt = (
            update(SpaceInviteCode)
            .where(
                SpaceInviteCode.id == code_id,
                SpaceInviteCode.deleted_at.is_(None),
                SpaceInviteCode.use_count < SpaceInviteCode.max_uses,
            )
            .values(
                use_count=SpaceInviteCode.use_count + 1,
                updated_at=datetime.now(UTC),
            )
        )
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0
