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
    ProjectRole,
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
        # 顺带把他在本项目各话题里的席位也撤掉。从前只删这一行，人就从名册上消失了
        # 却还是每个房间都进得来（``authorize_topic_access`` 认话题角色），移出项目
        # 于是成了一件没做完的事。退项目走的是同一条撤销（返回值在那儿有用，这里不看）。
        await TopicMemberService(self._session).revoke_project_seats(
            project_id=project_id, member_handle=user_handle
        )
        await self._repo.delete(member)

    async def _access_comes_from_the_team(self, project: Project, actor: Actor) -> bool:
        """他在这个项目里的位置，是所属小队给的吗。

        「小队带进来的」有两种长相：名册上读时补出来的 ``source: "team"`` 那一行
        （没有成员行），和 ``ProjectService._seed_roster`` 在建项目时替小队成员落下
        的那份真行。只挡前一种是不够的 —— 后一种删得掉，删完人也没走：小队第二天
        照样把他带回来，重新读一次名册他又在，接口却回了成功。他真正退得出的是小
        队，所以两种都挡在这里。

        没有 ``team_id`` 的项目（旧数据）不进这一支：那时小队带不出访问，他走了就
        是走了。解析不出用户行的 handle（脚本、夹具）同理 —— 没有 uid 就谈不上是
        不是小队成员，按「不是」走，别把路堵死。
        """
        if project.team_id is None:
            return False
        from app.domain.team.services import team_service
        from app.domain.user.services import user_by_handle

        user_id = actor.user_id
        if user_id is None:
            user = await user_by_handle(self._session, actor.handle)
            if user is None:
                return False
            user_id = user.id
        return await team_service(self._session).is_team_member(
            project.team_id, user_id
        )

    async def leave(self, *, project_id: uuid.UUID, actor: Actor) -> None:
        """退出项目 —— 成员自己走的那条路。

        今天只有 owner / lead 能把人移出去，成员自己想走只能去求人。而项目成员身份
        是进得来这个项目**全部话题**的凭据，所以「离开」必须是一条本人可用的路。

        和写名册那三条路不同，这里没有 manager 授权：动作的对象恒是动作人自己
        （身份从 resolver 来，代退没有入口）。被拒的几种情况各自对应一条真实的死
        路，所以每一种都说得出下一步：

        - 身份没核实（403，和写名册那三条路同一种拒绝）：连「你是谁」都没说清。
        - 所有者：他在名册上只有 ``list_members`` 读时补出来的那一行，一走项目就
          没人管得了 —— 得先把项目转让给别人。文案只说这一步，不承诺界面上有个按
          钮等着他：能转让的只有 ``PUT /projects/{id}/owner``，界面还没有入口。
        - 访问来自所属小队（``_access_comes_from_the_team``）：在小队里退，不在这里。
        - 某个话题的最后一个 owner：``revoke_project_seats`` 拒绝并点名那间房。
        - 什么都不是：没有成员行、在本项目也没有任何席位、名册上也没有他 —— 409，
          和 ``leave_group`` 的「Not a member」一样。

        次序是有意的，**每一个拒绝都在动手之前**：所有者和小队那两条在最前面，席位
        的撤销自己会在删任何东西之前拒掉「最后一个 owner」；最后那条「什么都不是」
        也只在撤销的返回值为空（即一个字节都没动）时才说出口。

        所以「名册上没有成员行」不能当作「你不是成员」：所有者从来不落成员行，项目
        转让之后他手上就只剩话题席位了（总览那一份），而他确实还进得来那些房间。用
        那句答复他，既不是事实，又把他那份残留留在库里 —— 他走得掉，席位跟着走。
        """
        project = await self._ensure_project(project_id)
        if not actor.authenticated:
            raise ForbiddenError("需要登录后才能退出项目")
        if project.owner_handle and project.owner_handle == actor.handle:
            raise ForbiddenError("项目所有者不能退出项目，需要先把项目转让给别人")
        if await self._access_comes_from_the_team(project, actor):
            raise ConflictError(
                "你对这个项目的访问来自所属小队，退出项目要在小队里操作"
            )
        member = await self._repo.get(project_id=project_id, user_handle=actor.handle)
        revoked = await TopicMemberService(self._session).revoke_project_seats(
            project_id=project_id, member_handle=actor.handle
        )
        # 名册的三个来源到这里都问过了：成员行（上面这行）、所有者（前面那两条）、
        # 小队（``_access_comes_from_the_team``）。两样都没有，他才真的和这个项目
        # 没有关系。
        if member is None and not revoked:
            raise ConflictError("Not a member")
        if member is not None:
            await self._repo.delete(member)


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
        """「已经在项目里」= 完整名册，不是 ``project_members`` 那一张表。

        名册还有两个来源：项目所有者，和所属小队的成员——两者都不写行，读的时候
        补出来（``ProjectRepository.list_members`` 说得很清楚）。只查表的话，一个
        已经通过小队在项目里的人会被再邀请一次；而接受之后落下的那一行，会在他退
        队之后继续生效，正是那条注释要避免的事。
        """
        return any(
            m["handle"] == handle for m in await self._projects.list_members(project_id)
        )

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
        await _reject_execution_identity(self._session, invitee_handle)
        if await self._is_on_project_roster(project_id, invitee_handle):
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
