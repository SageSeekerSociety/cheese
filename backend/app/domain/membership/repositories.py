"""Project membership data access."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.models import (
    InvitationStatus,
    ProjectInvitation,
    ProjectMember,
    ProjectMemberExclusion,
)


class MemberRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, *, project_id: uuid.UUID, user_handle: str) -> ProjectMember:
        """Put ``user_handle`` on the roster — and lift 「他不在这个项目里」.

        显式加人就是这条事实的反面：退出之后再被放上名册（``MemberService.seat_agent``
        的座位行、接受邀请时 ``InvitationService`` 落的那一行）说明他又在这个项目里
        了。两条加人的路最后都落到这里，所以清在这里就是清在两条路上；而它同时是
        「有成员行 ⇒ 没被排除」这条不变式的守门人（``may_read_project`` 先认成员行
        再看排除，靠的就是它）。
        """
        await self.lift_exclusion(project_id=project_id, user_handle=user_handle)
        member = ProjectMember(project_id=project_id, user_handle=user_handle)
        self._session.add(member)
        await self._session.flush()
        await self._session.refresh(member)
        return member

    async def is_excluded(self, *, project_id: uuid.UUID, user_handle: str) -> bool:
        """Is this person out of this project even though the team may say otherwise?"""
        stmt = select(ProjectMemberExclusion.id).where(
            ProjectMemberExclusion.project_id == project_id,
            ProjectMemberExclusion.user_handle == user_handle,
        )
        return await self._session.scalar(stmt) is not None

    async def excluded_handles(self, project_id: uuid.UUID) -> set[str]:
        """Everyone excluded from this project, in one read — the roster's half."""
        stmt = select(ProjectMemberExclusion.user_handle).where(
            ProjectMemberExclusion.project_id == project_id
        )
        return set((await self._session.scalars(stmt)).all())

    async def exclude(self, *, project_id: uuid.UUID, user_handle: str) -> None:
        """Record 「他在小队里，但不属于这个项目」.

        ``ON CONFLICT DO NOTHING`` 而不是先查后写：这条事实是**幂等**的（说过一次
        就够），而两个人同时按「退出项目」不该撞出一句 IntegrityError。
        """
        await self._session.execute(
            pg_insert(ProjectMemberExclusion)
            .values(project_id=project_id, user_handle=user_handle)
            .on_conflict_do_nothing(constraint="uq_project_member_exclusion")
        )
        await self._session.flush()

    async def lift_exclusion(self, *, project_id: uuid.UUID, user_handle: str) -> None:
        """Take 「他不在这个项目里」 back off this person's name."""
        await self._session.execute(
            delete(ProjectMemberExclusion).where(
                ProjectMemberExclusion.project_id == project_id,
                ProjectMemberExclusion.user_handle == user_handle,
            )
        )
        await self._session.flush()

    async def get(
        self, *, project_id: uuid.UUID, user_handle: str
    ) -> ProjectMember | None:
        stmt = select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_handle == user_handle,
        )
        return await self._session.scalar(stmt)

    async def list_for_project(self, project_id: uuid.UUID) -> list[ProjectMember]:
        stmt = (
            select(ProjectMember)
            .where(ProjectMember.project_id == project_id)
            .order_by(ProjectMember.created_at.asc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def count_for_project(self, project_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(ProjectMember)
            .where(ProjectMember.project_id == project_id)
        )
        return int((await self._session.scalar(stmt)) or 0)

    async def delete(self, member: ProjectMember) -> None:
        await self._session.delete(member)
        await self._session.flush()


class InvitationRepository:
    """项目邀请的数据访问。

    答复过的邀请不删：这一行是「谁在什么时候把谁拉进来的」的唯一记录。
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        invitee_handle: str,
        inviter_handle: str,
    ) -> ProjectInvitation:
        invitation = ProjectInvitation(
            project_id=project_id,
            invitee_handle=invitee_handle,
            inviter_handle=inviter_handle,
            status=InvitationStatus.pending,
        )
        self._session.add(invitation)
        await self._session.flush()
        await self._session.refresh(invitation)
        return invitation

    async def get(self, invitation_id: uuid.UUID) -> ProjectInvitation | None:
        return await self._session.get(ProjectInvitation, invitation_id)

    async def pending_for(
        self, *, project_id: uuid.UUID, invitee_handle: str
    ) -> ProjectInvitation | None:
        stmt = select(ProjectInvitation).where(
            ProjectInvitation.project_id == project_id,
            ProjectInvitation.invitee_handle == invitee_handle,
            ProjectInvitation.status == InvitationStatus.pending,
        )
        return await self._session.scalar(stmt)

    async def list_pending_for_project(
        self, project_id: uuid.UUID
    ) -> list[ProjectInvitation]:
        stmt = (
            select(ProjectInvitation)
            .where(
                ProjectInvitation.project_id == project_id,
                ProjectInvitation.status == InvitationStatus.pending,
            )
            .order_by(ProjectInvitation.created_at.asc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_pending_for_person(self, handle: str) -> list[ProjectInvitation]:
        stmt = (
            select(ProjectInvitation)
            .where(
                ProjectInvitation.invitee_handle == handle,
                ProjectInvitation.status == InvitationStatus.pending,
            )
            .order_by(ProjectInvitation.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def settle(
        self, invitation: ProjectInvitation, status: InvitationStatus
    ) -> ProjectInvitation:
        invitation.status = status
        invitation.responded_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.refresh(invitation)
        return invitation
