"""Project membership data access."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_facts import recall, remember
from app.domain.project.models import (
    InvitationStatus,
    ProjectInvitation,
    ProjectMember,
    ProjectMemberExclusion,
    ProjectRole,
    ProjectRoleDef,
    ProjectTransfer,
)
from app.domain.topic.models import Topic

_ROLE_DEF_TABLES = frozenset({"project_role_defs"})


def _can_cache(session: object) -> bool:
    return isinstance(session, AsyncSession)


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
        if self._session.autoflush:
            await self._session.flush()
        key = ("excluded_handles", project_id)
        found, cached = recall(self._session, key)
        if found:
            return cached
        stmt = select(ProjectMemberExclusion.user_handle).where(
            ProjectMemberExclusion.project_id == project_id
        )
        handles = set((await self._session.scalars(stmt)).all())
        remember(
            self._session,
            key,
            handles,
            tables=frozenset({"project_member_exclusions"}),
        )
        return handles

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
        role_id: uuid.UUID | None = None,
        invited_by_assistant: bool = False,
    ) -> ProjectInvitation:
        """``role_id`` is one of the project's own roles; none means member."""
        invitation = ProjectInvitation(
            project_id=project_id,
            invitee_handle=invitee_handle,
            inviter_handle=inviter_handle,
            invited_by_assistant=invited_by_assistant,
            status=InvitationStatus.pending,
            role="custom" if role_id is not None else "member",
            role_id=role_id,
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

    async def waiting_for_role(self, role_id: uuid.UUID) -> bool:
        """Whether an invitation still waiting would give this role."""
        return (
            await self._session.scalar(
                select(ProjectInvitation.id)
                .where(
                    ProjectInvitation.role_id == role_id,
                    ProjectInvitation.status == InvitationStatus.pending,
                )
                .limit(1)
            )
        ) is not None

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


def role_key(role: str, role_id: uuid.UUID | None) -> str:
    """The key a recorded role goes by: ``admin``, ``member``, or the id of
    one of the project's own roles."""
    return str(role_id) if role_id is not None else role


class RoleRepository:
    """The roles a project has, who holds which, and what each may do (#3830,
    #4795)."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def role_of(self, *, project_id: uuid.UUID, user_handle: str) -> str | None:
        """The key of the role recorded for ``user_handle``; none when nothing is."""
        row = (
            await self._session.execute(
                select(ProjectRole.role, ProjectRole.role_id).where(
                    ProjectRole.project_id == project_id,
                    ProjectRole.user_handle == user_handle,
                )
            )
        ).first()
        return role_key(row[0], row[1]) if row is not None else None

    async def roles(self, project_id: uuid.UUID) -> dict[str, str]:
        rows = await self._session.execute(
            select(
                ProjectRole.user_handle, ProjectRole.role, ProjectRole.role_id
            ).where(ProjectRole.project_id == project_id)
        )
        return {handle: role_key(role, rid) for handle, role, rid in rows.all()}

    async def set_role(
        self, *, project_id: uuid.UUID, user_handle: str, role_id: uuid.UUID | None
    ) -> None:
        """Record one of the project's own roles (``role_id``), or admin (none)."""
        role = "custom" if role_id is not None else "admin"
        await self._session.execute(
            pg_insert(ProjectRole)
            .values(
                project_id=project_id,
                user_handle=user_handle,
                role=role,
                role_id=role_id,
            )
            .on_conflict_do_update(
                constraint="uq_project_role",
                set_={
                    "role": role,
                    "role_id": role_id,
                    "updated_at": datetime.now(UTC),
                },
            )
        )
        await self._session.flush()

    async def clear_role(self, *, project_id: uuid.UUID, user_handle: str) -> None:
        await self._session.execute(
            delete(ProjectRole).where(
                ProjectRole.project_id == project_id,
                ProjectRole.user_handle == user_handle,
            )
        )
        await self._session.flush()

    async def holders(self, role_id: uuid.UUID) -> list[str]:
        """Who holds one of the project's own roles."""
        return list(
            (
                await self._session.scalars(
                    select(ProjectRole.user_handle).where(
                        ProjectRole.role_id == role_id
                    )
                )
            ).all()
        )

    async def move_holders(
        self, *, role_id: uuid.UUID, to_role_id: uuid.UUID | None, to_admin: bool
    ) -> None:
        """Everyone holding ``role_id``, and every invitation waiting to give it,
        moved to another role: one of the project's own, admin, or member
        (neither)."""
        held = ProjectRole.role_id == role_id
        if to_role_id is None and not to_admin:
            await self._session.execute(delete(ProjectRole).where(held))
        else:
            await self._session.execute(
                update(ProjectRole)
                .where(held)
                .values(
                    role="custom" if to_role_id is not None else "admin",
                    role_id=to_role_id,
                    updated_at=datetime.now(UTC),
                )
            )
        await self._session.execute(
            update(ProjectInvitation)
            .where(
                ProjectInvitation.role_id == role_id,
                ProjectInvitation.status == InvitationStatus.pending,
            )
            .values(
                role="custom" if to_role_id is not None else "member",
                role_id=to_role_id,
            )
        )
        await self._session.flush()

    async def replace_speaker(
        self, *, project_id: uuid.UUID, key: str, to_key: str | None
    ) -> None:
        """A role leaving the project: in every channel whose main line it could
        speak in, the role its holders moved to (``to_key``) speaks instead, or
        nobody does when it had no holders."""
        for room in (
            await self._session.scalars(
                select(Topic).where(
                    Topic.project_id == project_id, Topic.speaker_roles.is_not(None)
                )
            )
        ).all():
            keys = list(room.speaker_roles or [])
            if key not in keys:
                continue
            keys = [k for k in keys if k != key]
            if to_key is not None and to_key not in keys:
                keys.append(to_key)
            room.speaker_roles = keys
        await self._session.flush()

    async def defs(self, project_id: uuid.UUID) -> list[ProjectRoleDef]:
        """The project's role rows, its own roles in the order they were made."""
        if _can_cache(self._session):
            if self._session.autoflush:
                await self._session.flush()
            found, cached = recall(self._session, ("project_role_defs", project_id))
            if found:
                return list(cached)
        rows = list(
            (
                await self._session.scalars(
                    select(ProjectRoleDef)
                    .where(ProjectRoleDef.project_id == project_id)
                    .order_by(ProjectRoleDef.created_at, ProjectRoleDef.id)
                )
            ).all()
        )
        if _can_cache(self._session):
            remember(
                self._session,
                ("project_role_defs", project_id),
                tuple(rows),
                tables=_ROLE_DEF_TABLES,
            )
        return rows

    async def defs_of(
        self, project_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[ProjectRoleDef]]:
        out: dict[uuid.UUID, list[ProjectRoleDef]] = {pid: [] for pid in project_ids}
        for row in (
            await self._session.scalars(
                select(ProjectRoleDef)
                .where(ProjectRoleDef.project_id.in_(project_ids))
                .order_by(ProjectRoleDef.created_at, ProjectRoleDef.id)
            )
        ).all():
            out[row.project_id].append(row)
        return out

    async def get_def(
        self, *, project_id: uuid.UUID, role_id: uuid.UUID
    ) -> ProjectRoleDef | None:
        row = await self._session.get(ProjectRoleDef, role_id)
        return row if row is not None and row.project_id == project_id else None

    async def templated(
        self, *, project_id: uuid.UUID, template: str
    ) -> ProjectRoleDef | None:
        return await self._session.scalar(
            select(ProjectRoleDef)
            .where(
                ProjectRoleDef.project_id == project_id,
                ProjectRoleDef.template == template,
            )
            .order_by(ProjectRoleDef.created_at)
            .limit(1)
        )

    async def named(self, *, project_id: uuid.UUID, name: str) -> ProjectRoleDef | None:
        return await self._session.scalar(
            select(ProjectRoleDef).where(
                ProjectRoleDef.project_id == project_id, ProjectRoleDef.name == name
            )
        )

    async def add_def(
        self,
        *,
        project_id: uuid.UUID,
        name: str,
        permissions: list[str],
        template: str | None = None,
    ) -> ProjectRoleDef:
        row = ProjectRoleDef(
            project_id=project_id, name=name, permissions=permissions, template=template
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def delete_def(self, row: ProjectRoleDef) -> None:
        await self._session.delete(row)
        await self._session.flush()

    async def set_builtin_grants(
        self, *, project_id: uuid.UUID, builtin: str, permissions: list[str]
    ) -> None:
        await self._session.execute(
            pg_insert(ProjectRoleDef)
            .values(project_id=project_id, builtin=builtin, permissions=permissions)
            .on_conflict_do_update(
                constraint="uq_project_role_def_builtin",
                set_={"permissions": permissions, "updated_at": datetime.now(UTC)},
            )
        )
        await self._session.flush()

    async def clear_builtin_grants(
        self, *, project_id: uuid.UUID, builtin: str
    ) -> None:
        """A built-in role back to its defaults."""
        await self._session.execute(
            delete(ProjectRoleDef).where(
                ProjectRoleDef.project_id == project_id,
                ProjectRoleDef.builtin == builtin,
            )
        )
        await self._session.flush()


class TransferRepository:
    """Offers to hand a project to someone, pending until they answer."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self, *, project_id: uuid.UUID, from_handle: str, to_handle: str
    ) -> ProjectTransfer:
        offer = ProjectTransfer(
            project_id=project_id, from_handle=from_handle, to_handle=to_handle
        )
        self._session.add(offer)
        await self._session.flush()
        await self._session.refresh(offer)
        return offer

    async def get(self, transfer_id: uuid.UUID) -> ProjectTransfer | None:
        return await self._session.get(ProjectTransfer, transfer_id)

    async def pending_for_project(
        self, project_id: uuid.UUID
    ) -> ProjectTransfer | None:
        return await self._session.scalar(
            select(ProjectTransfer).where(
                ProjectTransfer.project_id == project_id,
                ProjectTransfer.status == InvitationStatus.pending,
            )
        )

    async def pending_for_person(self, handle: str) -> list[ProjectTransfer]:
        rows = await self._session.scalars(
            select(ProjectTransfer)
            .where(
                ProjectTransfer.to_handle == handle,
                ProjectTransfer.status == InvitationStatus.pending,
            )
            .order_by(ProjectTransfer.created_at.desc())
        )
        return list(rows.all())

    async def settle(
        self, offer: ProjectTransfer, status: InvitationStatus
    ) -> ProjectTransfer:
        offer.status = status
        offer.responded_at = datetime.now(UTC)
        await self._session.flush()
        return offer
