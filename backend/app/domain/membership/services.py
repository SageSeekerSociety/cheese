"""Project membership business logic.

Every write here is authorized against the caller: the roster decides who can
reach the project's topics at all (``authorize_topic_access``), so ``actor`` is
a required argument, not an optional courtesy — a future caller that forgets to
pass one fails to compile rather than silently writing unauthorized.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.domain.authz.policy import can_manage_project_members
from app.domain.identity.actor import Actor
from app.domain.membership.repositories import InvitationRepository, MemberRepository
from app.domain.project.models import (
    InvitationStatus,
    Project,
    ProjectInvitation,
    ProjectMember,
)
from app.domain.project.repositories import ProjectRepository
from app.domain.topic_membership.services import TopicMemberService


async def _reject_execution_identity(session: AsyncSession, handle: str) -> None:
    """执行身份不能被**当人邀请**进项目——只挡邀请这条路。

    agent-user 是**执行身份**（这一次动作里是谁在说话），不是一个人；长期 Agent 记
    在 ``agent_instances`` 上、由项目的 Agent 配置管理。成员页那个「邀请成员」先按
    uid 查人、再拿 handle 发邀请，两步都不区分两者，于是能把 AI 队友当人请进来——
    同一件事的第二个入口，两边的授权很快会不一致。

    **不挡直加名册**（``MemberService.add``）：它是平台给 agent 放座位的原语，也是
    接受邀请时真正落行的地方（见 ``InvitationService`` 的 docstring），agent 因此照
    常出现在名册上、带 ``agent`` 标记，成员页的「AI 队友」那一栏读的就是它。

    判定复用 ``IdentityService.is_agent``：带 ``agent_bindings`` 行的才算 agent，不
    去看 handle 长得像不像（``cheese-<话题hex>`` 是派生格式，不是契约）。解析不出用
    户的 handle 一律放行——脚本和测试夹具会加这种，它们不是 agent。
    """
    from app.domain.identity.services import IdentityService

    if await IdentityService(session).is_agent(handle):
        raise ValidationError(
            "AI 队友不能加到项目成员里。项目里的 Agent 由 Agent 配置管理，不占人名册。"
        )


class MemberService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = MemberRepository(session)
        self._projects = ProjectRepository(session)

    async def get(
        self, *, project_id: uuid.UUID, user_handle: str
    ) -> ProjectMember | None:
        return await self._repo.get(project_id=project_id, user_handle=user_handle)

    async def _ensure_project(self, project_id: uuid.UUID) -> Project:
        """The project, or 404. Returns the row because the callers read it right
        after (the owner's handle, whether it has a team), and re-reading it to
        get what this already fetched is a second round trip for nothing."""
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError("Project not found")
        return project

    async def manages(self, project_id: uuid.UUID, handle: str) -> bool:
        """Is ``handle`` someone who manages this project — its owner, or an owner
        or admin of the project's team? For callers that hold a handle, not an
        authenticated actor; :meth:`require_manager` is the gate for a request."""
        project = await self._projects.get(project_id)
        if project is None:
            return False
        if project.owner_handle == handle:
            return True
        return await self._team_admin(project, handle)

    async def _team_admin(self, project: Project, handle: str) -> bool:
        from app.domain.team.services import team_service
        from app.domain.user.services import user_by_handle

        user = await user_by_handle(self._session, handle)
        if user is None:
            return False
        return await team_service(self._session).is_team_at_least_admin(
            project.team_id, user.id
        )

    async def require_manager(self, project_id: uuid.UUID, actor: Actor) -> None:
        """Only the project's owner or an owner/admin of its team (verified by
        token) may manage it. Called AFTER ``_ensure_project`` so a missing
        project still reads as 404 rather than 403 for everyone."""

        async def owner_of(pid: uuid.UUID) -> str | None:
            project = await self._projects.get(pid)
            return project.owner_handle if project is not None else None

        async def team_manager(pid: uuid.UUID, handle: str) -> bool:
            project = await self._projects.get(pid)
            return project is not None and await self._team_admin(project, handle)

        allowed = await can_manage_project_members(
            actor,
            project_id=project_id,
            project_owner=owner_of,
            team_manager=team_manager,
        )
        if not allowed:
            raise ForbiddenError("只有项目所有者或团队管理员能管理这个项目")

    async def seat_agent(
        self, *, project_id: uuid.UUID, user_handle: str, actor: Actor
    ) -> ProjectMember:
        """Give an AI teammate's execution identity a place on the roster.

        People never come in this way: a person outside the team is an external
        member only through an invitation they accept (:class:`InvitationService`),
        and a person on the team is already in every project of it.
        """
        await self._ensure_project(project_id)
        await self.require_manager(project_id, actor)
        from app.domain.identity.services import IdentityService

        if not await IdentityService(self._session).is_agent(user_handle):
            raise ValidationError("人通过邀请加入项目；这里只能给 AI 队友放座位")
        existing = await self._repo.get(project_id=project_id, user_handle=user_handle)
        if existing is not None:
            raise ValidationError("User is already a member of this project")
        return await self._repo.add(project_id=project_id, user_handle=user_handle)

    async def list_for_project(
        self, project_id: uuid.UUID
    ) -> tuple[list[ProjectMember], int]:
        await self._ensure_project(project_id)
        return (
            await self._repo.list_for_project(project_id),
            await self._repo.count_for_project(project_id),
        )

    async def remove(
        self, *, project_id: uuid.UUID, user_handle: str, actor: Actor
    ) -> None:
        """Take an external member (or an AI teammate's seat) out of the project.

        Team members have no row here: they are in the project because they are
        in the team, and they leave it by leaving the team.
        """
        await self._ensure_project(project_id)
        await self.require_manager(project_id, actor)
        member = await self._repo.get(project_id=project_id, user_handle=user_handle)
        if member is None:
            raise NotFoundError("Member not found")
        # Their seats in this project's rooms go too: a room seat admits on its
        # own (``authorize_topic_access`` reads the topic role), so leaving them
        # behind would leave the person in every room they had joined.
        await TopicMemberService(self._session).revoke_project_seats(
            project_id=project_id, member_handle=user_handle
        )
        await self._repo.delete(member)

    async def leave(self, *, project_id: uuid.UUID, actor: Actor) -> None:
        """An external member leaves the project on their own.

        Refused, each before anything is written: an unverified caller; the
        owner, who must first hand the project to someone else; a team member,
        whose access is the team's and ends by leaving the team; and anyone with
        neither a row nor a seat here. Removing the last owner of a room is
        refused inside ``revoke_project_seats``, before it deletes anything.
        """
        project = await self._ensure_project(project_id)
        if not actor.authenticated:
            raise ForbiddenError("需要登录后才能退出项目")
        if project.owner_handle and project.owner_handle == actor.handle:
            raise ForbiddenError("项目所有者不能退出项目，需要先把项目转让给别人")
        member = await self._repo.get(project_id=project_id, user_handle=actor.handle)
        if member is None and await self._on_team(project, actor):
            raise ConflictError("你是这个团队的成员，退出团队才会离开它的项目")
        revoked = await TopicMemberService(self._session).revoke_project_seats(
            project_id=project_id, member_handle=actor.handle
        )
        if member is None and not revoked:
            raise ConflictError("Not a member")
        if member is not None:
            await self._repo.delete(member)

    async def _on_team(self, project: Project, actor: Actor) -> bool:
        from app.domain.team.services import team_service

        if actor.user_id is None:
            return False
        return await team_service(self._session).is_team_member(
            project.team_id, actor.user_id
        )


# The two answers on an invitee's decision card, affirmative first. Choosing one
# on the card is the answer itself (``routes/alerts.resolve_notification``).
INVITATION_OPTIONS = ("接受", "拒绝")


class InvitationService:
    """邀请 —— 加人这件事的另一半。

    直接把一个 handle 放上名册（``MemberService.add``）这条路仍然在，它是这里踩着
    的地基（接受之后就是它把人放上去的），也是脚本和测试用的原语。差别在于**谁做的
    决定**：进了项目就看得见这个项目的全部话题，那是别人的工作内容，所以从界面上
    加人得由被加的那个人点头。

    还有一条只属于邀请的规矩：执行身份（agent-user）不能被**当人**请进来，理由见
    ``_reject_execution_identity``。
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

    async def _is_on_project_roster(self, project_id: uuid.UUID, handle: str) -> bool:
        """「已经在项目里」问的是名册，不是 ``project_members`` 那一张表。

        名册还有别的来源：项目所有者、所属小队的成员、这个项目的队友——都不写成员
        行，读的时候合出来（``membership.roster.roster``）。只查表的话，一个已经通
        过小队在项目里的人会被再邀请一次；而接受之后落下的那一行，会在他退队之后
        继续生效，正是要避免的事。
        """
        from app.domain.membership.roster import roster

        return any(m.handle == handle for m in await roster(self._session, project_id))

    async def invite(
        self,
        *,
        project_id: uuid.UUID,
        invitee_handle: str,
        actor: Actor,
    ) -> ProjectInvitation:
        project = await self._project_or_404(project_id)
        # 同一条授权，和改名册用的是同一个判断：能管名册的人才能发邀请。
        await MemberService(self._session).require_manager(project_id, actor)
        if not invitee_handle:
            raise ValidationError("要邀请谁")
        if actor.handle == invitee_handle:
            raise ValidationError("不用邀请自己")
        await _reject_execution_identity(self._session, invitee_handle)
        if await self._is_on_project_roster(project_id, invitee_handle):
            # Someone on the team is already in every project of it; an invitation
            # is only for a person from outside it.
            raise ValidationError("这个人已经在项目里了（团队成员不需要邀请）")
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
        )
        # 一条强提醒，而且是**待办**：decision_request 在被答复之前不会从收件箱里
        # 消失（resolved_at 才让它消失），正好是「等你回一句」这种东西该有的样子。
        # 函数体里 import：`notification.services` 在模块头 import 本模块（广播
        # 要按名册展开），两边都写在模块头就是一个导入环。
        from app.domain.notification.models import NotificationLevel, NotificationType
        from app.domain.notification.services import ProjectNotificationService

        await ProjectNotificationService(self._session).create(
            project_id=project_id,
            level=NotificationLevel.strong,
            kind=NotificationType.DECISION_REQUEST,
            title=f"{actor.handle} 邀请你加入项目「{project.name}」",
            body="接受之后你会以外部成员的身份加入这个项目，能看到它的公开房间。",
            target_handle=invitee_handle,
            payload={
                "invitation_id": str(invitation.id),
                "project_name": project.name,
                "options": list(INVITATION_OPTIONS),
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
        # And who it is for, so a pending invitation reads as a person, not a
        # bare handle, on the project's members page.
        invitee = await self._projects.person(invitation.invitee_handle)
        row["invitee_name"] = invitee["name"]
        row["invitee_avatar_id"] = invitee["avatar_id"]
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
            # 拦在执行身份上，而不是只拦在发邀请那一刻：一条**在修复之前**发出去的
            # 邀请还躺在那里，点一下接受就能绕开 invite 里那条判断。
            await _reject_execution_identity(self._session, invitation.invitee_handle)
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

        from app.domain.notification.models import Notification

        stmt = select(Notification).where(
            Notification.project_id == invitation.project_id,
            Notification.recipient_handle == invitation.invitee_handle,
            Notification.resolved_at.is_(None),
        )
        for row in (await self._session.scalars(stmt)).all():
            if (row.metadata_payload or {}).get("invitation_id") == str(invitation.id):
                row.resolved_at = datetime.now(UTC)
                payload = dict(row.metadata_payload or {})
                payload["resolved_choice"] = invitation.status.value
                row.metadata_payload = payload
        await self._session.flush()
