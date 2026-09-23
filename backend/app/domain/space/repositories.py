from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, and_, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

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

    async def default_category_shells(
        self, *, space_ids: Sequence[int]
    ) -> dict[int, str | None]:
        """Every one of these 题目板's default 分组 壳, in one query.

        A board is a course when this column says so (`app.domain.shell.catalog`
        decides which names mean that), and the list page asks it of a whole page
        of boards at once — one round trip per row is what this avoids.

        A board with no default 分组 (or an archived/dangling one) answers None,
        which reads as 「not a course」 rather than raising: the column is how the
        answer travels, not a guarantee about the row.
        """
        if not space_ids:
            return {}
        stmt = (
            select(Space.id, SpaceCategory.shell)
            .select_from(Space)
            .outerjoin(SpaceCategory, SpaceCategory.id == Space.default_category_id)
            .where(Space.id.in_(space_ids), Space.deleted_at.is_(None))
        )
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
    ) -> Space:
        now = datetime.now(UTC)
        space = Space(
            name=name,
            intro=intro,
            description=description,
            avatar_id=avatar_id,
            enable_rank=enable_rank,
            visible_task_limit=visible_task_limit,
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
        shell: str | None = None,
    ) -> SpaceCategory:
        now = datetime.now(UTC)
        category = SpaceCategory(
            space_id=space_id,
            name=name,
            description=description,
            display_order=display_order,
            shell=shell,
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

    async def add_member(
        self, *, space_id: int, user_id: int
    ) -> tuple[SpaceMember, bool]:
        """Join, or re-join after having been removed. Returns (row, created).

        A removed member keeps a soft-deleted row, which is why this revives
        it instead of inserting a second one — otherwise the same person
        would accumulate a row per attempt and `list_members` would have to
        know to deduplicate.

        ``created`` answers "was this call the one that let them in". Only
        that one consumes an invite-code use, so a double-tapped「加入」does
        not spend two.

        Both halves are written so the database, not a preceding read, decides
        that. Two requests for the same person do arrive together — a double
        tap, or an admin adding someone while they redeem a code — and a
        read-then-write pair lets both read "not a member" and both write:

        * Nothing there yet: they race on the insert and
          ``uq_space_member_active`` lets exactly one through. The loser's
          ``IntegrityError`` is the answer to the race rather than a failure,
          so it re-reads and returns the row the winner wrote. The insert
          runs inside a savepoint, which is what leaves the session usable.
        * A removed row: the revive is one conditional UPDATE, and only a
          ``rowcount`` of 1 means this call is the one that un-deleted it.
        """
        now = datetime.now(UTC)
        member = await self._get_any_member(space_id, user_id)

        if member is None:
            member = SpaceMember(
                space_id=space_id,
                user_id=user_id,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            self._session.add(member)
            try:
                async with self._session.begin_nested():
                    await self._session.flush()
            except IntegrityError:
                existing = await self._get_any_member(space_id, user_id)
                if existing is None:
                    # Not this race — a constraint we know nothing about.
                    # Better a 500 than a membership that only looks granted.
                    raise
                return existing, False
            return member, True

        stmt = (
            update(SpaceMember)
            .where(
                SpaceMember.id == member.id,
                SpaceMember.deleted_at.is_not(None),
            )
            .values(deleted_at=None, updated_at=now)
        )
        result = await self._session.execute(stmt)
        # UPDATE returns a CursorResult, which has rowcount at runtime.
        created = (result.rowcount or 0) > 0  # type: ignore[attr-defined]
        # Refreshed either way: on the created path to load what the UPDATE
        # wrote, and otherwise because the row this session already holds may
        # predate another request's revive and would be returned still marked
        # deleted.
        await self._session.refresh(member)
        return member, created

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
        """Spend one use, only if there is one left and the code has not expired.

        The limit is enforced inside the UPDATE rather than by reading the
        row first: two people redeeming the last use at the same moment both
        read "0 of 1" and both get in otherwise. Returns False when the
        UPDATE matched nothing — exhausted, deleted, or expired.

        Expiry belongs in the same predicate for the same reason the count
        does: `join_space` reads the row before calling this, so a code that
        expires in between would otherwise still be spent. Checking it here,
        in the statement that decides, is what makes the refusal real rather
        than a property of how far apart two reads happened to be.
        """
        stmt = (
            update(SpaceInviteCode)
            .where(
                SpaceInviteCode.id == code_id,
                SpaceInviteCode.deleted_at.is_(None),
                SpaceInviteCode.use_count < SpaceInviteCode.max_uses,
                or_(
                    SpaceInviteCode.expires_at.is_(None),
                    SpaceInviteCode.expires_at > datetime.now(UTC),
                ),
            )
            .values(
                use_count=SpaceInviteCode.use_count + 1,
                updated_at=datetime.now(UTC),
            )
        )
        result = await self._session.execute(stmt)
        # UPDATE returns a CursorResult, which has rowcount at runtime.
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]
