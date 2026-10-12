"""Who a person says they are in the new-user guide, and how an AI teammate reads it.

The guide asks what someone mainly uses the platform for and, optionally, their
school or employer. Both live on the account (`User.work_role`,
`User.organization`) and go onto every project roster an AI teammate reads
(`agent/turn/intake/rooms.room_roster`), so 芝士 knows who it is working with in
any project, not only the first one.
"""

from collections.abc import Iterable
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.user.models import User
from app.domain.user.repositories import UserProfileRepository
from app.domain.user.services import UserProfileService

# The guide's choices, in its order.
WorkRole = Literal["student", "teacher", "office", "research", "maker", "other"]

# How the roster names each choice to an AI teammate; "other" says nothing.
WORK_ROLES: dict[str, str] = {
    "student": "学生",
    "teacher": "老师或助教",
    "office": "上班族",
    "research": "研究者",
    "maker": "做自己的项目",
    "other": "",
}


async def about_by_handles(
    session: AsyncSession, handles: Iterable[str]
) -> dict[str, str]:
    """handle -> 「学生，中国人民大学」; people who said nothing are left out."""
    wanted = set(handles)
    if not wanted:
        return {}
    rows = await session.execute(
        select(User.username, User.work_role, User.organization).where(
            User.username.in_(wanted), User.deleted_at.is_(None)
        )
    )
    about: dict[str, str] = {}
    for handle, role, organization in rows.all():
        parts = [WORK_ROLES.get(role or "", ""), (organization or "").strip()]
        text = "，".join(p for p in parts if p)
        if text:
            about[handle] = text
    return about


async def record_guide_answers(
    session: AsyncSession,
    user_id: int,
    *,
    nickname: str | None,
    work_role: str | None,
    organization: str | None,
) -> User | None:
    """Keep what the guide was told, with the account row locked until the
    transaction ends: two tabs finishing the guide together stamp
    `onboarded_at` once (`POST /users/me/onboarding`). None if the account is gone.
    The caller stamps `onboarded_at` once the guide's project is made."""
    user = await session.scalar(
        select(User).where(User.id == user_id).with_for_update()
    )
    if user is None:
        return None
    if work_role is not None:
        user.work_role = work_role
    if organization is not None:
        user.organization = organization.strip() or None
    if nickname and nickname.strip():
        await UserProfileService(UserProfileRepository(session)).update_profile(
            user_id=user.id, nickname=nickname
        )
    return user
