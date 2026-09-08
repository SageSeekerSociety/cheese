"""Project membership data access."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.models import (
    InvitationStatus,
    ProjectInvitation,
    ProjectMember,
    ProjectRole,
)


class MemberRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self, *, project_id: uuid.UUID, user_handle: str, role: ProjectRole
    ) -> ProjectMember:
        member = ProjectMember(
            project_id=project_id, user_handle=user_handle, role=role
        )
        self._session.add(member)
        await self._session.flush()
        await self._session.refresh(member)
        return member

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

    async def update_role(
        self, member: ProjectMember, *, role: ProjectRole
    ) -> ProjectMember:
        member.role = role
        await self._session.flush()
        await self._session.refresh(member)
        return member

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
        role: ProjectRole,
    ) -> ProjectInvitation:
        invitation = ProjectInvitation(
            project_id=project_id,
            invitee_handle=invitee_handle,
            inviter_handle=inviter_handle,
            role=role,
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
