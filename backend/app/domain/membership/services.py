"""Project membership business logic.

Every write here is authorized against the caller: the roster decides who can
reach the project's topics at all (``authorize_topic_access``), so ``actor`` is
a required argument, not an optional courtesy — a future caller that forgets to
pass one fails to compile rather than silently writing unauthorized.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.project_roles import (
    is_owner,
    is_personal,
    may,
    owner_of,
    require,
    require_above,
    require_owner,
    role_grants,
    standing,
)
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.addressing import Event, Hand, address
from app.domain.authz.policy import can_manage_project_members
from app.domain.delivery_events import (
    Deliver,
    DeliveryEvent,
    NotificationType,
    Settle,
    event_id_for,
)
from app.domain.identity.actor import Actor
from app.domain.identity.services import IdentityService
from app.domain.membership.ports import ProjectTeam
from app.domain.membership.repositories import (
    InvitationRepository,
    MemberRepository,
    RoleRepository,
    TransferRepository,
)
from app.domain.membership.roster import roster
from app.domain.membership.schemas import InvitationOut
from app.domain.project.models import (
    InvitationStatus,
    Project,
    ProjectInvitation,
    ProjectMember,
    ProjectTransfer,
)
from app.domain.project.reads import project_or_404
from app.domain.project.repositories import ProjectRepository
from app.domain.project_permissions import (
    DEFAULT_GRANTS,
    MANAGING,
    PERMISSION_ORDER,
    Permission,
    Role,
    Seat,
    with_locks,
)
from app.domain.team.services import team_service
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.services import user_by_handle

#: What a role may be granted, by name.
_ROLE_PERMISSION_NAMES = frozenset(p.value for p in PERMISSION_ORDER)


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

    if await IdentityService(session).is_agent(handle):
        raise ValidationError(say("agentNotProjectMember"))


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
        return await project_or_404(self._projects, project_id)

    async def manages(
        self, project_id: uuid.UUID, handle: str, permission: Permission
    ) -> bool:
        """May ``handle`` do ``permission`` in this project? For callers that hold
        a handle, not an authenticated actor; :meth:`require` is the gate for a
        request."""
        return await may(self._session, project_id, handle, permission)

    async def _team_admin(self, project: Project, handle: str) -> bool:
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
            raise ForbiddenError(say("projectManageForbidden"))

    async def require(
        self, project_id: uuid.UUID, actor: Actor, permission: Permission
    ) -> None:
        """Refuse unless the caller, verified by token, may do ``permission`` here.

        A scoped credential (``via="cheese"``) is something running unattended on
        a person's behalf, not the person in their own session: it never manages
        the project, whatever the person may do. Called AFTER ``_ensure_project``
        so a missing project still reads as 404 rather than 403 for everyone."""
        if not actor.authenticated:
            raise ForbiddenError(say("projectPermissionDenied"))
        if actor.via == "cheese" and permission in MANAGING:
            raise ForbiddenError(say("projectPermissionDenied"))
        await require(self._session, project_id, actor.handle, permission)

    async def seat_agent(
        self, *, project_id: uuid.UUID, user_handle: str, actor: Actor
    ) -> ProjectMember:
        """Give an AI teammate's execution identity a place on the roster.

        People never come in this way: a person outside the team is an external
        member only through an invitation they accept (:class:`InvitationService`),
        and a person on the team is already in every project of it — except the
        ones they left, which is what an invitation gets them back into.
        """
        await self._ensure_project(project_id)
        await self.require(project_id, actor, Permission.configure_agents)

        if not await IdentityService(self._session).is_agent(user_handle):
            raise ValidationError(say("peopleJoinByInvite"))
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
        """Take someone out of the project: a person below the caller, or an AI
        teammate's seat.

        A person on the team is taken out the way they would leave: the project
        records that they are not in it (``ProjectMemberExclusion``) and the team
        is untouched. Their recorded role goes with them, so coming back by an
        invitation starts over from the role the invitation names.
        """
        project = await self._ensure_project(project_id)
        await self.require(project_id, actor, Permission.manage_members)
        if user_handle == actor.handle:
            raise UnprocessableEntityError(say("removeSelfByLeaving"))
        member = await self._repo.get(project_id=project_id, user_handle=user_handle)
        if not await IdentityService(self._session).is_agent(user_handle):
            await require_above(self._session, project_id, actor.handle, user_handle)
        on_team = await self._on_team(project, user_handle)
        if member is None and not on_team:
            raise NotFoundError()
        # Their seats in this project's rooms go too: a room seat admits on its
        # own (``authorize_topic_access`` reads the topic role), so leaving them
        # behind would leave the person in every room they had joined.
        await TopicMemberService(self._session).revoke_project_seats(
            project_id=project_id, member_handle=user_handle
        )
        if member is not None:
            await self._repo.delete(member)
        if on_team:
            await self._repo.exclude(project_id=project_id, user_handle=user_handle)
        await RoleRepository(self._session).clear_role(
            project_id=project_id, user_handle=user_handle
        )

    async def leave(self, *, project_id: uuid.UUID, actor: Actor) -> None:
        """Anyone in this project leaves it on their own — the project, not the team.

        「退出项目」退的是这个项目。它以前只对外部成员成立：队友的访问读时从小队
        继承，「在小队里」和「在这个项目里」是同一句话，于是队友按下去只能拿到
        409（去退小队）。现在按下它写下 ``(project_id, handle)`` 那条项目级事实
        （``ProjectMemberExclusion``），人还在小队里、名额和别的小队项目一概不动。

        Refused, each before anything is written: an unverified caller; the owner,
        who must first hand the project to someone else; and anyone with neither a
        row, a seat, nor the team. Removing the last owner of a room other people
        still sit in is refused inside ``revoke_project_seats``, before it deletes
        anything — and that refusal writes nothing here either, including the
        exclusion.
        """
        project = await self._ensure_project(project_id)
        if not actor.authenticated:
            raise ForbiddenError(say("leaveProjectSignIn"))
        if await is_owner(self._session, project_id, actor.handle):
            raise ForbiddenError(say("ownerCannotLeave"))
        member = await self._repo.get(project_id=project_id, user_handle=actor.handle)
        on_team = await self._on_team(project, actor.handle)
        if member is None and not on_team:
            # 只在小队/名册之外的人：他在这份名册上只剩房间席位（建房间时给的），
            # 撤掉就是退出；一个席位也没有，说明他和这个项目本来就没关系。
            revoked = await TopicMemberService(self._session).revoke_project_seats(
                project_id=project_id, member_handle=actor.handle
            )
            if not revoked:
                raise ConflictError("Not a member")
            return
        # 席位先撤：它是**进得来这个项目的全部话题**的另一张凭据，只删名册那一行
        # 的话人还是每个房间都进得去。这一步的拒绝（某间房最后一个 owner）落在这里
        # 之前，所以它一抛，这条事实也好、上面那两处也好，一个字节都还没动。
        await TopicMemberService(self._session).revoke_project_seats(
            project_id=project_id, member_handle=actor.handle
        )
        if member is not None:
            await self._repo.delete(member)
        if on_team:
            # 「他在小队里，但不属于这个项目」：小队那条主张读时继承，一行也不删
            # （那是团队的事），记下的是这个项目这边的那句话。
            await self._repo.exclude(project_id=project_id, user_handle=actor.handle)

    async def _on_team(self, project: Project, handle: str | None) -> bool:
        user = await user_by_handle(self._session, handle) if handle else None
        if user is None:
            return False
        return await team_service(self._session).is_team_member(
            project.team_id, user.id
        )


async def pending_invitations_for(
    session: AsyncSession, handle: str
) -> list[ProjectInvitation]:
    """请 ``handle`` 加入、他还没回答的项目邀请，新的在前。给「等你处理」读：只读，
    用不着 ``InvitationService`` 要的投递与结账那两只手。"""
    return await InvitationRepository(session).list_pending_for_person(handle)


class InvitationService:
    """邀请 —— 加人这件事的另一半。

    直接把一个 handle 放上名册（``MemberService.add``）这条路仍然在，它是这里踩着
    的地基（接受之后就是它把人放上去的），也是脚本和测试用的原语。差别在于**谁做的
    决定**：进了项目就看得见这个项目的全部话题，那是别人的工作内容，所以从界面上
    加人得由被加的那个人点头。

    还有一条只属于邀请的规矩：执行身份（agent-user）不能被**当人**请进来，理由见
    ``_reject_execution_identity``。
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        deliver: Deliver,
        settle: Settle,
        team: ProjectTeam,
    ):
        self._session = session
        self._team = team
        self._repo = InvitationRepository(session)
        self._members = MemberRepository(session)
        self._projects = ProjectRepository(session)
        # 邀请发出去是一条事件，被回应之后那条事件要结掉：两下都落在第 5 层的
        # 投递账本上，由构造这一侧递进来（`Deliver`、`Settle`）。
        self._deliver = deliver
        self._settle = settle

    async def _project_or_404(self, project_id: uuid.UUID):
        return await project_or_404(self._projects, project_id)

    async def _is_on_project_roster(self, project_id: uuid.UUID, handle: str) -> bool:
        """「已经在项目里」问的是名册，不是 ``project_members`` 那一张表。

        名册还有别的来源：项目所有者、所属小队的成员、这个项目的队友——都不写成员
        行，读的时候合出来（``membership.roster.roster``）。只查表的话，一个已经通
        过小队在项目里的人会被再邀请一次；而接受之后落下的那一行，会在他退队之后
        继续生效，正是要避免的事。

        反过来说，「退出项目」的队友在这里就是要回答「不在」——他不在名册上，所以
        邀请得出去，而接受那一步（``respond``）落下的名册行会把
        ``ProjectMemberExclusion`` 清掉（``MemberRepository.add``），这就是回来的
        那条路。
        """

        return any(
            m.handle == handle
            for m in await roster(self._session, project_id, team=self._team)
        )

    async def invite(
        self,
        *,
        project_id: uuid.UUID,
        invitee_handle: str,
        actor: Actor,
        role: Role = Role.member,
    ) -> ProjectInvitation:
        project = await self._project_or_404(project_id)
        if role not in (Role.member, Role.mentor):
            # An admin is made from someone already in the project.
            raise UnprocessableEntityError(say("inviteRole"))
        # 同一条授权，和改名册用的是同一个判断：能管名册的人才能发邀请。
        await MemberService(self._session).require(
            project_id, actor, Permission.manage_members
        )
        if not invitee_handle:
            raise ValidationError(say("inviteWhom"))
        if actor.handle == invitee_handle:
            raise ValidationError(say("inviteSelf"))
        if await user_by_handle(self._session, invitee_handle) is None:
            # An invitation is answered by the account it names. One to a
            # handle nobody holds reaches no one, and would admit whoever took
            # that handle later.
            raise ValidationError(say("inviteNoSuchPerson", handle=invitee_handle))
        await _reject_execution_identity(self._session, invitee_handle)
        if await self._is_on_project_roster(project_id, invitee_handle):
            # Someone on the team is already in every project of it; an invitation
            # is only for a person from outside it.
            raise ValidationError(say("inviteAlreadyMember"))
        if (
            await self._repo.pending_for(
                project_id=project_id, invitee_handle=invitee_handle
            )
            is not None
        ):
            raise ValidationError(say("invitePending"))
        invitation = await self._repo.add(
            project_id=project_id,
            invitee_handle=invitee_handle,
            inviter_handle=actor.handle or "",
            role=role.value,
            invited_by_assistant=actor.by_assistant,
        )
        await self._notify_invitee(invitation, project)
        return invitation

    async def _notify_invitee(
        self, invitation: ProjectInvitation, project: Project
    ) -> None:
        """Tell the invitee in their own mail — the bell, email — not the project's.

        They are not on the roster yet, so the project inbox is the one place they
        cannot open. The row carries the invitation id so it can be answered from
        the notification itself, and is settled once the invitation is
        (``_settle_notification``).
        """

        payload: dict = {
            "project": {"type": "project", "id": str(project.id)},
            "projectName": project.name,
            "invitationId": str(invitation.id),
        }
        inviter = await user_by_handle(self._session, invitation.inviter_handle)
        if inviter is not None:
            payload["inviter"] = {"type": "user", "id": str(inviter.id)}
        await self._deliver(
            self._session,
            DeliveryEvent(
                id=event_id_for(NotificationType.PROJECT_INVITE, str(invitation.id)),
                type=NotificationType.PROJECT_INVITE,
                payload=payload,
                occurred_at=invitation.created_at,
            ),
            address(Event(reviewers=(invitation.invitee_handle,)), Hand.participant),
        )

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
            raise ValidationError(say("inviteAnswered"))
        return invitation

    async def respond(
        self, *, invitation_id: uuid.UUID, accept: bool, actor: Actor
    ) -> ProjectInvitation:
        invitation = await self._pending_or_404(invitation_id)
        if actor.handle != invitation.invitee_handle:
            raise ForbiddenError(say("inviteNotYours"))
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
            roles = RoleRepository(self._session)
            if invitation.role == Role.mentor.value:
                await roles.set_role(
                    project_id=invitation.project_id,
                    user_handle=invitation.invitee_handle,
                    role=Role.mentor.value,
                )
            else:
                await roles.clear_role(
                    project_id=invitation.project_id,
                    user_handle=invitation.invitee_handle,
                )
        settled = await self._repo.settle(
            invitation,
            InvitationStatus.accepted if accept else InvitationStatus.declined,
        )
        await self._settle_notification(settled)
        return settled

    async def revoke(
        self, *, invitation_id: uuid.UUID, actor: Actor
    ) -> ProjectInvitation:
        invitation = await self._pending_or_404(invitation_id)
        await MemberService(self._session).require(
            invitation.project_id, actor, Permission.manage_members
        )
        settled = await self._repo.settle(invitation, InvitationStatus.revoked)
        await self._settle_notification(settled)
        return settled

    async def revoke_all_pending(self, project_id: uuid.UUID) -> None:
        """Withdraw every invitation still waiting on this project — archiving
        it does, as its owner. Authorized by the caller, like the archive is."""
        for invitation in await self._repo.list_pending_for_project(project_id):
            settled = await self._repo.settle(invitation, InvitationStatus.revoked)
            await self._settle_notification(settled)

    async def _settle_notification(self, invitation: ProjectInvitation) -> None:
        """The invitee's notification stops asking once the invitation is over.

        Answered or withdrawn, it is marked read and records how it ended, so it
        no longer offers to accept an invitation that has no answer left.
        """
        await self._settle(
            self._session,
            event_id_for(NotificationType.PROJECT_INVITE, str(invitation.id)),
            {"status": invitation.status.value},
        )


class RoleService:
    """Roles in a project and what each role may do there (#3830)."""

    def __init__(self, session: AsyncSession):
        self._session = session
        self._roles = RoleRepository(session)
        self._members = MemberService(session)

    async def set_role(
        self, *, project_id: uuid.UUID, handle: str, role: Role, actor: Actor
    ) -> None:
        """Make ``handle`` an admin, a member or a mentor.

        Only someone above them may, and the owner is above everyone. Making
        someone an admin is the owner's alone, like the team's own admins are
        its owner's: an admin manages members and mentors. The role is recorded
        only when it differs from the one they would hold anyway, so a member
        made admin and back holds no row.
        """
        await self._members._ensure_project(project_id)
        await self._members.require(project_id, actor, Permission.change_roles)
        if handle == actor.handle:
            raise UnprocessableEntityError(say("roleNotYourOwn"))
        current = await standing(self._session, project_id, handle)
        if current is None:
            raise NotFoundError()
        if await IdentityService(self._session).is_agent(handle):
            raise UnprocessableEntityError(say("roleNotForAgents"))
        await require_above(self._session, project_id, actor.handle, handle)
        if role is Role.admin and not await is_owner(
            self._session, project_id, actor.handle
        ):
            raise ForbiddenError(say("adminByOwnerOnly"))
        await self._roles.clear_role(project_id=project_id, user_handle=handle)
        held = await standing(self._session, project_id, handle)
        if held is not None and held.seat.value != role.value:
            await self._roles.set_role(
                project_id=project_id, user_handle=handle, role=role.value
            )

    async def grants(self, project_id: uuid.UUID) -> dict[Role, frozenset[Permission]]:
        return await role_grants(self._session, project_id)

    async def changed(self, project_id: uuid.UUID) -> bool:
        """Whether any role differs from its defaults here."""
        return bool(await self._roles.grants(project_id))

    async def set_grants(
        self,
        *,
        project_id: uuid.UUID,
        changed: dict[Role, list[str]],
        actor: Actor,
    ) -> None:
        """Change what roles may do. A name that is not a role's permission —
        one nobody knows, or one only an AI teammate holds — is refused; a
        locked permission keeps its place whatever was sent."""
        await self._members._ensure_project(project_id)
        await self._members.require(project_id, actor, Permission.change_roles)
        for role, names in changed.items():
            unknown = [n for n in names if n not in _ROLE_PERMISSION_NAMES]
            if unknown:
                raise UnprocessableEntityError(
                    say("permissionUnknown", name=unknown[0])
                )
            granted = with_locks(role, frozenset(Permission(n) for n in names))
            if granted == with_locks(role, DEFAULT_GRANTS[role]):
                await self._roles.clear_grants(project_id, role=role.value)
                continue
            await self._roles.set_grants(
                project_id=project_id,
                role=role.value,
                permissions=sorted(p.value for p in granted),
            )

    async def reset_grants(self, *, project_id: uuid.UUID, actor: Actor) -> None:
        await self._members._ensure_project(project_id)
        await self._members.require(project_id, actor, Permission.change_roles)
        await self._roles.clear_grants(project_id)


class OwnershipService:
    """The owner handing the project over, or moving their own project into a
    team (#3830).

    Handing over is an offer the recipient answers: nobody becomes a project's
    owner without saying yes. The recipient is someone already in the project
    and not a mentor. Once they accept, the former owner stays as an admin; a
    personal project moves into the recipient's own space, since it cannot stay
    in a space that is one other person's.
    """

    def __init__(self, session: AsyncSession, *, deliver: Deliver, settle: Settle):
        self._session = session
        self._offers = TransferRepository(session)
        self._roles = RoleRepository(session)
        self._members = MemberRepository(session)
        self._projects = ProjectRepository(session)
        self._deliver = deliver
        self._settle = settle

    async def _project(self, project_id: uuid.UUID) -> Project:
        return await project_or_404(self._projects, project_id)

    async def offer(
        self, *, project_id: uuid.UUID, to_handle: str, actor: Actor
    ) -> ProjectTransfer:
        project = await self._project(project_id)
        await self._require_owner_in_session(project_id, actor)
        if to_handle == actor.handle:
            raise UnprocessableEntityError(say("transferToSelf"))
        recipient = await standing(self._session, project_id, to_handle)
        if (
            recipient is None
            or recipient.seat is Seat.mentor
            or await IdentityService(self._session).is_agent(to_handle)
        ):
            raise UnprocessableEntityError(say("transferToMember"))
        pending = await self._offers.pending_for_project(project_id)
        if pending is not None:
            await self._close(pending, InvitationStatus.revoked)
        offer = await self._offers.add(
            project_id=project_id, from_handle=actor.handle, to_handle=to_handle
        )
        await self._deliver(
            self._session,
            DeliveryEvent(
                id=event_id_for(NotificationType.PROJECT_TRANSFER, str(offer.id)),
                type=NotificationType.PROJECT_TRANSFER,
                payload={
                    "project": {"type": "project", "id": str(project.id)},
                    "projectName": project.name,
                    "transferId": str(offer.id),
                    "from": actor.handle,
                },
                occurred_at=offer.created_at,
            ),
            address(Event(reviewers=(to_handle,)), Hand.participant),
        )
        return offer

    async def cancel(self, *, transfer_id: uuid.UUID, actor: Actor) -> ProjectTransfer:
        offer = await self._pending(transfer_id)
        await self._require_owner_in_session(offer.project_id, actor)
        return await self._close(offer, InvitationStatus.revoked)

    async def respond(
        self, *, transfer_id: uuid.UUID, accept: bool, actor: Actor
    ) -> ProjectTransfer:
        offer = await self._pending(transfer_id)
        if actor.handle != offer.to_handle:
            raise ForbiddenError(say("transferNotYours"))
        if not accept:
            return await self._close(offer, InvitationStatus.declined)
        project = await self._project(offer.project_id)
        recipient = await standing(self._session, project.id, offer.to_handle)
        if recipient is None or recipient.seat is Seat.mentor:
            # They left, or became a mentor, after the offer was made.
            await self._close(offer, InvitationStatus.revoked)
            raise UnprocessableEntityError(say("transferToMember"))
        previous = await owner_of(self._session, project)
        if await is_personal(self._session, project):
            user = await user_by_handle(self._session, offer.to_handle)
            if user is None:
                raise NotFoundError()
            team = await team_service(self._session).ensure_personal_team(user.id)
            project.team_id = team.id
            if (
                previous
                and await self._members.get(project_id=project.id, user_handle=previous)
                is None
            ):
                await self._members.add(project_id=project.id, user_handle=previous)
        project.owner_handle = offer.to_handle
        await self._session.flush()
        await self._roles.clear_role(project_id=project.id, user_handle=offer.to_handle)
        if previous and previous != offer.to_handle:
            await self._roles.set_role(
                project_id=project.id, user_handle=previous, role=Role.admin.value
            )
        return await self._close(offer, InvitationStatus.accepted)

    async def move_to_team(
        self, *, project_id: uuid.UUID, team_id: int, actor: Actor
    ) -> Project:
        """Move the owner's own project into a team they manage. Everyone in it
        stays: the team's people join as members, the others keep their seat."""
        project = await self._project(project_id)
        await self._require_owner_in_session(project_id, actor)
        if not await is_personal(self._session, project):
            raise UnprocessableEntityError(say("moveOnlyPersonal"))
        user = await user_by_handle(self._session, actor.handle)
        if user is None or not await team_service(self._session).is_team_at_least_admin(
            team_id, user.id
        ):
            raise ForbiddenError(say("moveNeedsTeamAdmin"))
        project.team_id = team_id
        await self._session.flush()
        return project

    async def pending_for(self, project_id: uuid.UUID) -> ProjectTransfer | None:
        return await self._offers.pending_for_project(project_id)

    async def _require_owner_in_session(
        self, project_id: uuid.UUID, actor: Actor
    ) -> None:
        """The owner in their own session. Someone not in the project learns
        nothing about it, not even that it is there."""
        if (
            not actor.authenticated
            or await standing(self._session, project_id, actor.handle) is None
        ):
            raise NotFoundError(say("projectNotFound"))
        if actor.via == "cheese":
            raise ForbiddenError(say("projectOwnerOnly"))
        await require_owner(self._session, project_id, actor.handle)

    async def _pending(self, transfer_id: uuid.UUID) -> ProjectTransfer:
        offer = await self._offers.get(transfer_id)
        if offer is None:
            raise NotFoundError()
        if offer.status != InvitationStatus.pending:
            raise UnprocessableEntityError(say("transferAnswered"))
        return offer

    async def _close(
        self, offer: ProjectTransfer, status: InvitationStatus
    ) -> ProjectTransfer:
        settled = await self._offers.settle(offer, status)
        await self._settle(
            self._session,
            event_id_for(NotificationType.PROJECT_TRANSFER, str(offer.id)),
            {"status": settled.status.value},
        )
        return settled


async def pending_transfers_for(
    session: AsyncSession, handle: str
) -> list[ProjectTransfer]:
    """Offers waiting on ``handle`` to take a project over, newest first."""
    return await TransferRepository(session).pending_for_person(handle)


async def seat_mentors(
    session: AsyncSession, project_id: uuid.UUID, handles: Sequence[str]
) -> None:
    """Put a task's mentors in the project made for a claim: on its roster, as
    mentors. Someone already in the project keeps where they sit."""
    members = MemberRepository(session)
    roles = RoleRepository(session)
    for handle in handles:
        if await standing(session, project_id, handle) is not None:
            continue
        await members.add(project_id=project_id, user_handle=handle)
        await roles.set_role(
            project_id=project_id, user_handle=handle, role=Role.mentor.value
        )
