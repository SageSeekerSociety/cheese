"""Project membership business logic.

Every write here is authorized against the caller: the roster decides who can
reach the project's topics at all (``authorize_topic_access``), so ``actor`` is
a required argument, not an optional courtesy — a future caller that forgets to
pass one fails to compile rather than silently writing unauthorized.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.authz.policy import can_manage_project_members
from app.domain.identity.actor import Actor
from app.domain.membership.repositories import InvitationRepository, MemberRepository
from app.domain.project.models import (
    InvitationStatus,
    ProjectInvitation,
    ProjectMember,
    ProjectRole,
)
from app.domain.project.repositories import ProjectRepository


class MemberService:
    def __init__(self, session: AsyncSession):
        self._repo = MemberRepository(session)
        self._projects = ProjectRepository(session)

    async def _ensure_project(self, project_id: uuid.UUID) -> None:
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")

    async def require_manager(self, project_id: uuid.UUID, actor: Actor) -> None:
        """Only the project's owner or a lead (verified by token) may write the
        roster. Called AFTER ``_ensure_project`` so a missing project still reads
        as 404 rather than 403 for everyone."""

        async def owner_of(pid: uuid.UUID) -> str | None:
            project = await self._projects.get(pid)
            return project.owner_handle if project is not None else None

        async def role_of(pid: uuid.UUID, handle: str) -> ProjectRole | None:
            member = await self._repo.get(project_id=pid, user_handle=handle)
            return member.role if member is not None else None

        allowed = await can_manage_project_members(
            actor,
            project_id=project_id,
            project_owner=owner_of,
            project_role=role_of,
        )
        if not allowed:
            raise ForbiddenError("只有项目的 owner / lead 能管理项目成员")

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        user_handle: str,
        role: ProjectRole,
        actor: Actor,
    ) -> ProjectMember:
        await self._ensure_project(project_id)
        await self.require_manager(project_id, actor)
        existing = await self._repo.get(project_id=project_id, user_handle=user_handle)
        if existing is not None:
            raise ValidationError("User is already a member of this project")
        return await self._repo.add(
            project_id=project_id, user_handle=user_handle, role=role
        )

    async def ensure_member(
        self, *, project_id: uuid.UUID, user_handle: str, role: ProjectRole
    ) -> ProjectMember | None:
        """把一个人放上名册，已经在上面就什么也不做。

        **没有 actor，因为这不是谁发起的写**：它只有一个调用场景——建项目时按小队
        铺名册。那一刻还没有请求者在对这个项目做动作，硬凑一个演员出来只会让「谁被
        授权做了什么」这件事变得更难读。所有由人发起的加人都走 ``add``，那条路要
        actor、也会授权。

        幂等，所以重复铺不会撞上唯一约束。
        """
        existing = await self._repo.get(project_id=project_id, user_handle=user_handle)
        if existing is not None:
            return None
        return await self._repo.add(
            project_id=project_id, user_handle=user_handle, role=role
        )

    async def list_for_project(
        self, project_id: uuid.UUID
    ) -> tuple[list[ProjectMember], int]:
        await self._ensure_project(project_id)
        return (
            await self._repo.list_for_project(project_id),
            await self._repo.count_for_project(project_id),
        )

    async def update_role(
        self,
        *,
        project_id: uuid.UUID,
        user_handle: str,
        role: ProjectRole,
        actor: Actor,
    ) -> ProjectMember:
        await self._ensure_project(project_id)
        await self.require_manager(project_id, actor)
        member = await self._repo.get(project_id=project_id, user_handle=user_handle)
        if member is None:
            raise NotFoundError("Member not found")
        return await self._repo.update_role(member, role=role)

    async def remove(
        self, *, project_id: uuid.UUID, user_handle: str, actor: Actor
    ) -> None:
        await self._ensure_project(project_id)
        await self.require_manager(project_id, actor)
        member = await self._repo.get(project_id=project_id, user_handle=user_handle)
        if member is None:
            raise NotFoundError("Member not found")
        await self._repo.delete(member)


class InvitationService:
    """邀请 —— 加人这件事的另一半。

    直接把一个 handle 放上名册（``MemberService.add``）这条路仍然在，它是这里踩着
    的地基（接受之后就是它把人放上去的），也是脚本和测试用的原语。差别在于**谁做的
    决定**：进了项目就看得见这个项目的全部话题，那是别人的工作内容，所以从界面上
    加人得由被加的那个人点头。
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = InvitationRepository(session)
        self._members = MemberRepository(session)
        self._projects = ProjectRepository(session)

    async def _project_or_404(self, project_id: uuid.UUID):
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError("Project not found")
        return project

    async def invite(
        self,
        *,
        project_id: uuid.UUID,
        invitee_handle: str,
        role: ProjectRole,
        actor: Actor,
    ) -> ProjectInvitation:
        project = await self._project_or_404(project_id)
        # 同一条授权，和改名册用的是同一个判断：能管名册的人才能发邀请。
        await MemberService(self._session).require_manager(project_id, actor)
        if not invitee_handle:
            raise ValidationError("要邀请谁")
        if actor.handle == invitee_handle:
            raise ValidationError("不用邀请自己")
        if (
            await self._members.get(project_id=project_id, user_handle=invitee_handle)
            is not None
        ):
            raise ValidationError("这个人已经在项目里了")
        if (
            await self._repo.pending_for(
                project_id=project_id, invitee_handle=invitee_handle
            )
            is not None
        ):
            raise ValidationError("已经邀请过这个人，正在等他答复")
        invitation = await self._repo.add(
            project_id=project_id,
            invitee_handle=invitee_handle,
            inviter_handle=actor.handle or "",
            role=role,
        )
        # 一条强提醒，而且是**待办**：decision_request 在被答复之前不会从收件箱里
        # 消失（resolved_at 才让它消失），正好是「等你回一句」这种东西该有的样子。
        from app.domain.alert.models import AlertKind, AlertLevel
        from app.domain.alert.services import AlertService

        await AlertService(self._session).create(
            project_id=project_id,
            level=AlertLevel.strong,
            kind=AlertKind.decision_request,
            title=f"{actor.handle} 邀请你加入项目「{project.name}」",
            body="接受之后你能看到这个项目的全部话题。",
            target_handle=invitee_handle,
            payload={
                "invitation_id": str(invitation.id),
                "project_name": project.name,
                "role": role.value,
                "options": ["接受", "拒绝"],
            },
        )
        return invitation

    async def list_for_project(self, project_id: uuid.UUID) -> list[ProjectInvitation]:
        await self._project_or_404(project_id)
        return await self._repo.list_pending_for_project(project_id)

    async def list_for_person(self, handle: str) -> list[ProjectInvitation]:
        return await self._repo.list_pending_for_person(handle)

    async def describe(self, invitation: ProjectInvitation) -> dict:
        """邀请 + 项目名。

        名字得后端带出来：被邀请的人还不在这个项目里，任何一条项目作用域的接口他
        都够不着，自己配不出来。少了它，界面上只能显示「有人邀请你加入一个项目」
        ——一句让人没法决定接不接受的话。
        """
        from app.domain.membership.schemas import InvitationOut

        row = InvitationOut.model_validate(invitation).model_dump(mode="json")
        project = await self._projects.get(invitation.project_id)
        row["project_name"] = project.name if project else ""
        return row

    async def _pending_or_404(self, invitation_id: uuid.UUID) -> ProjectInvitation:
        invitation = await self._repo.get(invitation_id)
        if invitation is None:
            raise NotFoundError("Invitation not found")
        if invitation.status != InvitationStatus.pending:
            # 已经答复过的邀请不是「找不到」，说清楚它已经结束了——否则界面上那颗
            # 按钮点两下会得到一句莫名其妙的 404。
            raise ValidationError("这张邀请已经答复过了")
        return invitation

    async def respond(
        self, *, invitation_id: uuid.UUID, accept: bool, actor: Actor
    ) -> ProjectInvitation:
        invitation = await self._pending_or_404(invitation_id)
        if actor.handle != invitation.invitee_handle:
            raise ForbiddenError("只有被邀请的人能答复这张邀请")
        if accept:
            # 中间可能已经被人用别的路加进去了（脚本、直接调接口）。那不是错误，
            # 邀请照样算数，只是不用再加一次。
            existing = await self._members.get(
                project_id=invitation.project_id,
                user_handle=invitation.invitee_handle,
            )
            if existing is None:
                await self._members.add(
                    project_id=invitation.project_id,
                    user_handle=invitation.invitee_handle,
                    role=invitation.role,
                )
        settled = await self._repo.settle(
            invitation,
            InvitationStatus.accepted if accept else InvitationStatus.declined,
        )
        await self._resolve_alert(settled)
        return settled

    async def revoke(
        self, *, invitation_id: uuid.UUID, actor: Actor
    ) -> ProjectInvitation:
        invitation = await self._pending_or_404(invitation_id)
        await MemberService(self._session).require_manager(invitation.project_id, actor)
        settled = await self._repo.settle(invitation, InvitationStatus.revoked)
        await self._resolve_alert(settled)
        return settled

    async def _resolve_alert(self, invitation: ProjectInvitation) -> None:
        """把收件箱里那条待办结掉。

        不做的话，一张已经答复（或已经撤回）的邀请会永远挂在对方的收件箱里等他回
        答一个已经没有答案的问题。
        """
        from datetime import UTC, datetime

        from sqlalchemy import select

        from app.domain.alert.models import Alert

        stmt = select(Alert).where(
            Alert.project_id == invitation.project_id,
            Alert.target_handle == invitation.invitee_handle,
            Alert.resolved_at.is_(None),
        )
        for alert in (await self._session.scalars(stmt)).all():
            if (alert.payload or {}).get("invitation_id") == str(invitation.id):
                alert.resolved_at = datetime.now(UTC)
                payload = dict(alert.payload or {})
                payload["resolved_choice"] = invitation.status.value
                alert.payload = payload
        await self._session.flush()
