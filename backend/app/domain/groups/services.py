from datetime import UTC, datetime

from app.core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.domain.groups.models import Group, GroupProfile, GroupTarget
from app.domain.groups.repositories import (
    GroupMembershipRepository,
    GroupProfileRepository,
    GroupQuestionRepository,
    GroupRepository,
    GroupTargetRepository,
)
from app.domain.user.repositories import UserProfileRepository


def _group_to_dto(
    group: Group,
    profile: GroupProfile | None,
    *,
    member_count: int = 0,
    question_count: int = 0,
    answer_count: int = 0,
    owner: dict | None = None,
    is_member: bool = False,
    is_owner: bool = False,
) -> dict:
    created_at_ms = int(group.created_at.timestamp() * 1000) if group.created_at else 0
    updated_at_ms = int(group.updated_at.timestamp() * 1000) if group.updated_at else 0
    return {
        "id": group.id,
        "name": group.name,
        "intro": profile.intro if profile else "",
        "avatarId": profile.avatar_id if profile else None,
        "member_count": member_count,
        "question_count": question_count,
        "answer_count": answer_count,
        "owner": owner,
        "is_member": is_member,
        "is_owner": is_owner,
        "is_public": True,
        "created_at": created_at_ms,
        "updated_at": updated_at_ms,
    }


class GroupsService:
    def __init__(
        self,
        repo: GroupRepository,
        profile_repo: GroupProfileRepository,
        membership_repo: GroupMembershipRepository,
        user_profile_repo: UserProfileRepository,
    ) -> None:
        self._repo = repo
        self._profile_repo = profile_repo
        self._membership_repo = membership_repo
        self._user_profile_repo = user_profile_repo

    async def list_groups(
        self,
        *,
        keyword: str | None,
        page_start: int | None,
        page_size: int,
        user_id: int | None = None,
        joined: bool | None = None,
        managed: bool | None = None,
    ) -> tuple[list[dict], dict]:
        offset = page_start or 0
        rows, total = await self._repo.search(
            keyword=keyword,
            limit=page_size,
            offset=offset,
            user_id=user_id,
            joined=joined,
            managed=managed,
        )
        group_ids = [row.id for row in rows]
        profiles = await self._profile_repo.get_profiles_by_group_ids(group_ids)

        items = []
        for row in rows:
            profile = profiles.get(row.id)
            member_count = await self._membership_repo.count_members(row.id)
            items.append(_group_to_dto(row, profile, member_count=member_count))

        returned = len(items)
        has_more = offset + returned < total
        next_start = offset + returned if has_more and returned > 0 else None
        page = {
            "pageStart": offset,
            "pageSize": returned,
            "hasMore": has_more,
            "nextStart": next_start,
            "total": total,
        }
        return items, page

    async def create_group(
        self,
        *,
        user_id: int,
        name: str,
        intro: str,
        avatar_id: int | None,
    ) -> dict:
        if not name.strip():
            raise BadRequestError("name cannot be empty")
        group = await self._repo.create_group(name=name.strip())
        profile = await self._profile_repo.create_profile(
            group_id=group.id,
            intro=intro,
            avatar_id=avatar_id,
        )
        await self._membership_repo.add_member(
            group_id=group.id,
            member_id=user_id,
            role="OWNER",
        )
        user_profile = await self._user_profile_repo.get_profile_by_user_id(user_id)
        owner_dto = {"id": user_id}
        if user_profile:
            owner_dto["nickname"] = user_profile.nickname
            owner_dto["avatarId"] = user_profile.avatar_id
        return _group_to_dto(
            group,
            profile,
            member_count=1,
            question_count=0,
            answer_count=0,
            owner=owner_dto,
            is_member=True,
            is_owner=True,
        )

    async def get_group(self, group_id: int, user_id: int | None = None) -> dict:
        group = await self._repo.get_by_id(group_id)
        if group is None:
            raise NotFoundError("Group not found", data={"id": group_id})
        profile = await self._profile_repo.get_by_group_id(group_id)
        member_count = await self._membership_repo.count_members(group_id)
        owner_dto = await self._build_owner_dto(group_id)
        is_member = False
        is_owner = False
        if user_id:
            role = await self._membership_repo.get_member_role(group_id, user_id)
            is_member = role is not None
            is_owner = role == "OWNER"
        return _group_to_dto(
            group,
            profile,
            member_count=member_count,
            question_count=0,
            answer_count=0,
            owner=owner_dto,
            is_member=is_member,
            is_owner=is_owner,
        )

    async def update_group(
        self,
        *,
        group_id: int,
        user_id: int,
        name: str | None = None,
        intro: str | None = None,
        avatar_id: int | None = None,
    ) -> dict:
        group = await self._repo.get_by_id(group_id)
        if group is None:
            raise NotFoundError("Group not found", data={"id": group_id})
        role = await self._membership_repo.get_member_role(group_id, user_id)
        if role not in ("OWNER", "ADMIN"):
            raise ForbiddenError("Only owners and admins can update the group")
        if name and await self._repo.exists_by_name(name, exclude_id=group_id):
            raise ConflictError("Group name already exists")
        await self._repo.update_group(group, name=name)
        profile = await self._profile_repo.get_by_group_id(group_id)
        if profile:
            await self._profile_repo.update_profile(profile, intro=intro, avatar_id=avatar_id)
        member_count = await self._membership_repo.count_members(group_id)
        owner_dto = await self._build_owner_dto(group_id)
        is_member = role is not None
        is_owner = role == "OWNER"
        return _group_to_dto(
            group,
            profile,
            member_count=member_count,
            question_count=0,
            answer_count=0,
            owner=owner_dto,
            is_member=is_member,
            is_owner=is_owner,
        )

    async def delete_group(self, *, group_id: int, user_id: int) -> None:
        group = await self._repo.get_by_id(group_id)
        if group is None:
            raise NotFoundError("Group not found", data={"id": group_id})
        role = await self._membership_repo.get_member_role(group_id, user_id)
        if role != "OWNER":
            raise ForbiddenError("Only the owner can delete the group")
        await self._repo.soft_delete(group)

    async def _build_owner_dto(self, group_id: int) -> dict | None:
        owner_id = await self._membership_repo.get_owner_id(group_id)
        if not owner_id:
            return None
        owner_dto: dict = {"id": owner_id}
        owner_profile = await self._user_profile_repo.get_profile_by_user_id(owner_id)
        if owner_profile:
            owner_dto["nickname"] = owner_profile.nickname
            owner_dto["avatarId"] = owner_profile.avatar_id
        return owner_dto

    async def list_members(
        self,
        *,
        group_id: int,
        page_start: int | None,
        page_size: int,
    ) -> tuple[list[dict], dict]:
        group = await self._repo.get_by_id(group_id)
        if group is None:
            raise NotFoundError("Group not found", data={"id": group_id})

        if page_size <= 0:
            page = {
                "pageStart": 0,
                "pageSize": 0,
                "hasPrev": False,
                "prevStart": 0,
                "hasMore": False,
                "nextStart": 0,
            }
            return [], page

        memberships, prev_id, next_id = await self._membership_repo.list_members_cursor(
            group_id=group_id, cursor=page_start, limit=page_size
        )
        member_ids = {m.member_id for m in memberships}
        profiles = await self._user_profile_repo.get_profiles_by_user_ids(list(member_ids))

        members = []
        for m in memberships:
            profile = profiles.get(m.member_id)
            dto = {
                "id": m.member_id,
                "nickname": profile.nickname if profile else "",
                "avatarId": profile.avatar_id if profile else None,
                "intro": profile.intro if profile else "",
                "role": m.role,
            }
            members.append(dto)

        first_id = members[0]["id"] if members else 0
        page = {
            "pageStart": first_id,
            "pageSize": len(members),
            "hasPrev": prev_id is not None,
            "prevStart": prev_id if prev_id else 0,
            "hasMore": next_id is not None,
            "nextStart": next_id if next_id else 0,
        }
        return members, page

    async def join_group(self, *, group_id: int, user_id: int) -> dict:
        group = await self._repo.get_by_id(group_id)
        if group is None:
            raise NotFoundError("Group not found", data={"id": group_id})
        if await self._membership_repo.is_member(group_id, user_id):
            raise ConflictError("Already a member")
        await self._membership_repo.add_member(group_id=group_id, member_id=user_id, role="MEMBER")
        member_count = await self._membership_repo.count_members(group_id)
        return {"memberCount": member_count}

    async def leave_group(self, *, group_id: int, user_id: int) -> dict:
        group = await self._repo.get_by_id(group_id)
        if group is None:
            raise NotFoundError("Group not found", data={"id": group_id})
        role = await self._membership_repo.get_member_role(group_id, user_id)
        if role is None:
            raise ConflictError("Not a member")
        if role == "OWNER":
            raise ForbiddenError("Owner cannot leave the group")
        await self._membership_repo.remove_member(group_id=group_id, member_id=user_id)
        member_count = await self._membership_repo.count_members(group_id)
        return {"memberCount": member_count}


def _date_to_ms(d) -> int:
    if d is None:
        return 0
    if hasattr(d, "timestamp"):
        return int(d.timestamp() * 1000)
    from datetime import datetime as dt

    return int(dt.combine(d, dt.min.time(), tzinfo=UTC).timestamp() * 1000)


def _target_to_dto(target: GroupTarget) -> dict:
    started_at_ms = _date_to_ms(target.started_at)
    ended_at_ms = _date_to_ms(target.ended_at)
    created_at_ms = int(target.created_at.timestamp() * 1000) if target.created_at else 0
    return {
        "id": target.id,
        "groupId": target.group_id,
        "name": target.name,
        "intro": target.intro,
        "startedAt": started_at_ms,
        "endedAt": ended_at_ms,
        "attendanceFrequency": target.attendance_frequency,
        "createdAt": created_at_ms,
    }


class GroupTargetService:
    def __init__(
        self,
        group_repo: GroupRepository,
        target_repo: GroupTargetRepository,
        membership_repo: GroupMembershipRepository,
    ) -> None:
        self._group_repo = group_repo
        self._target_repo = target_repo
        self._membership_repo = membership_repo

    async def list_targets(
        self, *, group_id: int, page_start: int | None, page_size: int
    ) -> tuple[list[dict], dict]:
        group = await self._group_repo.get_by_id(group_id)
        if group is None:
            raise NotFoundError("Group not found", data={"id": group_id})
        offset = page_start or 0
        targets, total = await self._target_repo.list_by_group(
            group_id=group_id, limit=page_size, offset=offset
        )
        items = [_target_to_dto(t) for t in targets]
        returned = len(items)
        has_more = offset + returned < total
        next_start = offset + returned if has_more and returned > 0 else None
        page = {
            "pageStart": offset,
            "pageSize": returned,
            "hasMore": has_more,
            "nextStart": next_start,
            "total": total,
        }
        return items, page

    async def get_target(self, *, group_id: int, target_id: int) -> dict:
        target = await self._target_repo.get_by_id(target_id)
        if target is None or target.group_id != group_id:
            raise NotFoundError("Target not found", data={"id": target_id})
        return _target_to_dto(target)

    async def create_target(
        self,
        *,
        group_id: int,
        user_id: int,
        name: str,
        intro: str,
        started_at: datetime,
        ended_at: datetime,
        attendance_frequency: str,
    ) -> dict:
        group = await self._group_repo.get_by_id(group_id)
        if group is None:
            raise NotFoundError("Group not found", data={"id": group_id})
        role = await self._membership_repo.get_member_role(group_id, user_id)
        if role not in ("OWNER", "ADMIN"):
            raise ForbiddenError("Only owners and admins can create targets")
        target = await self._target_repo.create(
            group_id=group_id,
            name=name,
            intro=intro,
            started_at=started_at,
            ended_at=ended_at,
            attendance_frequency=attendance_frequency,
        )
        return {"id": target.id}

    async def update_target(
        self,
        *,
        group_id: int,
        target_id: int,
        user_id: int,
        name: str | None = None,
        intro: str | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        attendance_frequency: str | None = None,
    ) -> dict:
        target = await self._target_repo.get_by_id(target_id)
        if target is None or target.group_id != group_id:
            raise NotFoundError("Target not found", data={"id": target_id})
        role = await self._membership_repo.get_member_role(group_id, user_id)
        if role not in ("OWNER", "ADMIN"):
            raise ForbiddenError("Only owners and admins can update targets")
        await self._target_repo.update(
            target,
            name=name,
            intro=intro,
            started_at=started_at,
            ended_at=ended_at,
            attendance_frequency=attendance_frequency,
        )
        return _target_to_dto(target)

    async def delete_target(self, *, group_id: int, target_id: int, user_id: int) -> None:
        target = await self._target_repo.get_by_id(target_id)
        if target is None or target.group_id != group_id:
            raise NotFoundError("Target not found", data={"id": target_id})
        role = await self._membership_repo.get_member_role(group_id, user_id)
        if role not in ("OWNER", "ADMIN"):
            raise ForbiddenError("Only owners and admins can delete targets")
        await self._target_repo.soft_delete(target)


class GroupQuestionService:
    def __init__(
        self,
        group_repo: GroupRepository,
        question_repo: GroupQuestionRepository,
        membership_repo: GroupMembershipRepository,
    ) -> None:
        self._group_repo = group_repo
        self._question_repo = question_repo
        self._membership_repo = membership_repo

    async def list_questions(
        self, *, group_id: int, page_start: int | None, page_size: int
    ) -> tuple[list[int], dict]:
        group = await self._group_repo.get_by_id(group_id)
        if group is None:
            raise NotFoundError("Group not found", data={"id": group_id})
        offset = page_start or 0
        question_ids, total = await self._question_repo.list_by_group(
            group_id=group_id, limit=page_size, offset=offset
        )
        returned = len(question_ids)
        has_more = offset + returned < total
        next_start = offset + returned if has_more and returned > 0 else None
        page = {
            "pageStart": offset,
            "pageSize": returned,
            "hasMore": has_more,
            "nextStart": next_start,
            "total": total,
        }
        return question_ids, page

    async def add_question(self, *, group_id: int, question_id: int, user_id: int) -> dict:
        group = await self._group_repo.get_by_id(group_id)
        if group is None:
            raise NotFoundError("Group not found", data={"id": group_id})
        role = await self._membership_repo.get_member_role(group_id, user_id)
        if role not in ("OWNER", "ADMIN"):
            raise ForbiddenError("Only owners and admins can add questions")
        await self._question_repo.add_question(group_id=group_id, question_id=question_id)
        return {"questionId": question_id}

    async def remove_question(self, *, group_id: int, question_id: int, user_id: int) -> None:
        group = await self._group_repo.get_by_id(group_id)
        if group is None:
            raise NotFoundError("Group not found", data={"id": group_id})
        role = await self._membership_repo.get_member_role(group_id, user_id)
        if role not in ("OWNER", "ADMIN"):
            raise ForbiddenError("Only owners and admins can remove questions")
        await self._question_repo.remove_question(group_id=group_id, question_id=question_id)
