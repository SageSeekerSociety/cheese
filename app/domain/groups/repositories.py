from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession


from app.domain.groups.models import (
    Group,
    GroupProfile,
    GroupMembership,
    GroupTarget,
    GroupQuestionRelationship,
)


def _to_date(dt_or_date) -> date | None:
    if dt_or_date is None:
        return None
    if isinstance(dt_or_date, datetime):
        return dt_or_date.date()
    return dt_or_date


class GroupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_group(self, *, name: str) -> Group:
        now = datetime.now(timezone.utc)
        group = Group(
            name=name,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(group)
        await self._session.flush()
        return group

    async def get_by_id(self, group_id: int) -> Group | None:
        stmt: Select[tuple[Group]] = select(Group).where(
            Group.id == group_id,
            Group.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def search(
        self,
        *,
        keyword: str | None,
        limit: int,
        offset: int,
        user_id: int | None = None,
        joined: bool | None = None,
        managed: bool | None = None,
    ) -> tuple[list[Group], int]:
        stmt: Select[tuple[Group]] = select(Group).where(Group.deleted_at.is_(None))

        if keyword:
            like = f"%{keyword.strip()}%"
            stmt = stmt.where(Group.name.ilike(like))

        if joined and user_id:
            subq = select(GroupMembership.group_id).where(
                GroupMembership.member_id == user_id,
                GroupMembership.deleted_at.is_(None),
            )
            stmt = stmt.where(Group.id.in_(subq))

        if managed and user_id:
            subq = select(GroupMembership.group_id).where(
                GroupMembership.member_id == user_id,
                GroupMembership.role.in_(["OWNER", "ADMIN"]),
                GroupMembership.deleted_at.is_(None),
            )
            stmt = stmt.where(Group.id.in_(subq))

        stmt = stmt.order_by(Group.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        count_stmt = select(func.count(Group.id)).where(Group.deleted_at.is_(None))
        if keyword:
            like = f"%{keyword.strip()}%"
            count_stmt = count_stmt.where(Group.name.ilike(like))
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return rows, total

    async def update_group(self, group: Group, *, name: str | None = None) -> Group:
        if name is not None:
            group.name = name
        group.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return group

    async def exists_by_name(self, name: str, exclude_id: int | None = None) -> bool:
        stmt = select(Group.id).where(
            Group.name == name,
            Group.deleted_at.is_(None),
        )
        if exclude_id:
            stmt = stmt.where(Group.id != exclude_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def soft_delete(self, group: Group) -> None:
        group.deleted_at = datetime.now(timezone.utc)
        await self._session.flush()


class GroupProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_group_id(self, group_id: int) -> GroupProfile | None:
        stmt: Select[tuple[GroupProfile]] = select(GroupProfile).where(
            GroupProfile.group_id == group_id,
            GroupProfile.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_profiles_by_group_ids(
        self, group_ids: Sequence[int]
    ) -> dict[int, GroupProfile]:
        if not group_ids:
            return {}
        stmt: Select[tuple[GroupProfile]] = select(GroupProfile).where(
            GroupProfile.group_id.in_(list(group_ids)),
            GroupProfile.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        return {row.group_id: row for row in rows}

    async def create_profile(
        self, *, group_id: int, intro: str, avatar_id: int | None
    ) -> GroupProfile:
        now = datetime.now(timezone.utc)
        profile = GroupProfile(
            group_id=group_id,
            intro=intro,
            avatar_id=avatar_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(profile)
        await self._session.flush()
        return profile

    async def update_profile(
        self,
        profile: GroupProfile,
        *,
        intro: str | None = None,
        avatar_id: int | None = None,
    ) -> GroupProfile:
        if intro is not None:
            profile.intro = intro
        if avatar_id is not None:
            profile.avatar_id = avatar_id
        profile.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return profile


class GroupMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_member(
        self, *, group_id: int, member_id: int, role: str = "MEMBER"
    ) -> GroupMembership:
        existing = await self._get_membership(group_id, member_id)
        now = datetime.now(timezone.utc)
        if existing is not None:
            if existing.deleted_at is None:
                return existing
            existing.deleted_at = None
            existing.role = role
            existing.updated_at = now
            await self._session.flush()
            return existing
        membership = GroupMembership(
            group_id=group_id,
            member_id=member_id,
            role=role,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(membership)
        await self._session.flush()
        return membership

    async def remove_member(self, *, group_id: int, member_id: int) -> bool:
        membership = await self._get_membership(group_id, member_id)
        if membership is None or membership.deleted_at is not None:
            return False
        membership.deleted_at = datetime.now(timezone.utc)
        await self._session.flush()
        return True

    async def list_members(
        self, *, group_id: int, limit: int, offset: int
    ) -> tuple[list[GroupMembership], int]:
        stmt = (
            select(GroupMembership)
            .where(
                GroupMembership.group_id == group_id,
                GroupMembership.deleted_at.is_(None),
            )
            .order_by(GroupMembership.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        count_stmt = select(func.count(GroupMembership.id)).where(
            GroupMembership.group_id == group_id,
            GroupMembership.deleted_at.is_(None),
        )
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return rows, total

    async def list_members_cursor(
        self, *, group_id: int, cursor: int | None, limit: int
    ) -> tuple[list[GroupMembership], int | None, int | None]:
        base_stmt = (
            select(GroupMembership)
            .where(
                GroupMembership.group_id == group_id,
                GroupMembership.deleted_at.is_(None),
            )
            .order_by(GroupMembership.member_id.asc())
        )

        if cursor is not None:
            stmt = base_stmt.where(GroupMembership.member_id >= cursor).limit(limit)
        else:
            stmt = base_stmt.limit(limit)

        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        prev_member_id: int | None = None
        if cursor is not None and rows:
            prev_stmt = (
                select(GroupMembership.member_id)
                .where(
                    GroupMembership.group_id == group_id,
                    GroupMembership.deleted_at.is_(None),
                    GroupMembership.member_id < cursor,
                )
                .order_by(GroupMembership.member_id.desc())
                .limit(1)
            )
            prev_result = await self._session.execute(prev_stmt)
            prev_member_id = prev_result.scalar_one_or_none()
        elif cursor is None and not rows:
            pass
        elif cursor is not None and not rows:
            prev_stmt = (
                select(GroupMembership.member_id)
                .where(
                    GroupMembership.group_id == group_id,
                    GroupMembership.deleted_at.is_(None),
                    GroupMembership.member_id < cursor,
                )
                .order_by(GroupMembership.member_id.desc())
                .limit(1)
            )
            prev_result = await self._session.execute(prev_stmt)
            prev_member_id = prev_result.scalar_one_or_none()

        next_member_id: int | None = None
        if rows and len(rows) == limit:
            last_id = rows[-1].member_id
            next_stmt = (
                select(GroupMembership.member_id)
                .where(
                    GroupMembership.group_id == group_id,
                    GroupMembership.deleted_at.is_(None),
                    GroupMembership.member_id > last_id,
                )
                .order_by(GroupMembership.member_id.asc())
                .limit(1)
            )
            next_result = await self._session.execute(next_stmt)
            next_member_id = next_result.scalar_one_or_none()

        return rows, prev_member_id, next_member_id

    async def count_members(self, group_id: int) -> int:
        stmt = select(func.count(GroupMembership.id)).where(
            GroupMembership.group_id == group_id,
            GroupMembership.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def is_member(self, group_id: int, member_id: int) -> bool:
        membership = await self._get_membership(group_id, member_id)
        return membership is not None and membership.deleted_at is None

    async def get_member_role(self, group_id: int, member_id: int) -> str | None:
        membership = await self._get_membership(group_id, member_id)
        if membership is None or membership.deleted_at is not None:
            return None
        return membership.role

    async def get_owner_id(self, group_id: int) -> int | None:
        stmt = select(GroupMembership.member_id).where(
            GroupMembership.group_id == group_id,
            GroupMembership.role == "OWNER",
            GroupMembership.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_membership(
        self, group_id: int, member_id: int
    ) -> GroupMembership | None:
        stmt: Select[tuple[GroupMembership]] = select(GroupMembership).where(
            GroupMembership.group_id == group_id,
            GroupMembership.member_id == member_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


class GroupTargetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_group(
        self, *, group_id: int, limit: int, offset: int
    ) -> tuple[list[GroupTarget], int]:
        stmt = (
            select(GroupTarget)
            .where(
                GroupTarget.group_id == group_id,
                GroupTarget.deleted_at.is_(None),
            )
            .order_by(GroupTarget.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        count_stmt = select(func.count(GroupTarget.id)).where(
            GroupTarget.group_id == group_id,
            GroupTarget.deleted_at.is_(None),
        )
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return rows, total

    async def get_by_id(self, target_id: int) -> GroupTarget | None:
        stmt = select(GroupTarget).where(
            GroupTarget.id == target_id,
            GroupTarget.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        group_id: int,
        name: str,
        intro: str,
        started_at: datetime,
        ended_at: datetime,
        attendance_frequency: str,
    ) -> GroupTarget:
        now = datetime.now(timezone.utc)
        target = GroupTarget(
            group_id=group_id,
            name=name,
            intro=intro,
            started_at=_to_date(started_at),
            ended_at=_to_date(ended_at),
            attendance_frequency=attendance_frequency,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(target)
        await self._session.flush()
        return target

    async def update(
        self,
        target: GroupTarget,
        *,
        name: str | None = None,
        intro: str | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        attendance_frequency: str | None = None,
    ) -> GroupTarget:
        if name is not None:
            target.name = name
        if intro is not None:
            target.intro = intro
        if started_at is not None:
            target.started_at = _to_date(started_at)
        if ended_at is not None:
            target.ended_at = _to_date(ended_at)
        if attendance_frequency is not None:
            target.attendance_frequency = attendance_frequency
        target.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return target

    async def soft_delete(self, target: GroupTarget) -> None:
        target.deleted_at = datetime.now(timezone.utc)
        await self._session.flush()


class GroupQuestionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_group(
        self, *, group_id: int, limit: int, offset: int
    ) -> tuple[list[int], int]:
        stmt = (
            select(GroupQuestionRelationship.question_id)
            .where(
                GroupQuestionRelationship.group_id == group_id,
                GroupQuestionRelationship.deleted_at.is_(None),
            )
            .order_by(GroupQuestionRelationship.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        question_ids = [row for row in result.scalars().all()]

        count_stmt = select(func.count(GroupQuestionRelationship.id)).where(
            GroupQuestionRelationship.group_id == group_id,
            GroupQuestionRelationship.deleted_at.is_(None),
        )
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return question_ids, total

    async def add_question(self, *, group_id: int, question_id: int) -> GroupQuestionRelationship:
        existing = await self._get_relationship(group_id, question_id)
        now = datetime.now(timezone.utc)
        if existing is not None:
            if existing.deleted_at is None:
                return existing
            existing.deleted_at = None
            existing.updated_at = now
            await self._session.flush()
            return existing
        rel = GroupQuestionRelationship(
            group_id=group_id,
            question_id=question_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(rel)
        await self._session.flush()
        return rel

    async def remove_question(self, *, group_id: int, question_id: int) -> bool:
        rel = await self._get_relationship(group_id, question_id)
        if rel is None or rel.deleted_at is not None:
            return False
        rel.deleted_at = datetime.now(timezone.utc)
        await self._session.flush()
        return True

    async def _get_relationship(
        self, group_id: int, question_id: int
    ) -> GroupQuestionRelationship | None:
        stmt = select(GroupQuestionRelationship).where(
            GroupQuestionRelationship.group_id == group_id,
            GroupQuestionRelationship.question_id == question_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
