"""Project membership routes (nested under /api/projects).

The write routes decide who is a member of the project, and project membership
is what ``authorize_topic_access`` reads to let someone into every topic of that
project. So the acting identity is resolved at the trust boundary
(``ActorResolverDep``) and the service authorizes it — unlike most 2.0 routes
these do NOT honor a handle passed in the body: a claimed handle is exactly the
forgery this surface must not accept. Reading the roster used to stay open; it
does not any more (see ``list_members``). The invitation list is part of the
same surface - it names who is still expected to answer - and took the same
door late, because this docstring still promised the old behaviour.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok, page
from app.core.db import get_db
from app.domain.membership.join_links import JoinLinkService
from app.domain.membership.roster import roster
from app.domain.membership.schemas import (
    InvitationCreate,
    InvitationOut,
    InvitationRespond,
    JoinLinkSettings,
    JoinThroughLink,
    MemberCreate,
    MemberOut,
    MemberRoleUpdate,
)
from app.domain.membership.services import InvitationService, MemberService

router = APIRouter(prefix="", tags=["members"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("/projects/{project_id}/join-link")
async def get_join_link(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.require_verified_caller()
    link = await JoinLinkService(db).current(project_id, actor)
    await db.commit()
    return ok(link)


@router.patch("/projects/{project_id}/join-link")
async def update_join_link(
    project_id: uuid.UUID,
    body: JoinLinkSettings,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    actor = await resolver.require_verified_caller()
    link = await JoinLinkService(db).set_approval(project_id, body.approval, actor)
    await db.commit()
    return ok(link)


@router.post("/projects/{project_id}/join-link/reset")
async def reset_join_link(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.require_verified_caller()
    link = await JoinLinkService(db).reset(project_id, actor)
    await db.commit()
    return ok(link)


@router.get("/project-invites/{token}")
async def preview_join_link(
    token: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.require_verified_caller()
    return ok(await JoinLinkService(db).describe(token, actor))


@router.post("/project-invites/{token}/join")
async def join_through_link(
    token: str,
    db: DbSession,
    resolver: ActorResolverDep,
    body: JoinThroughLink | None = None,
) -> dict:
    actor = await resolver.require_verified_caller()
    result = await JoinLinkService(db).join(
        token, actor, message=body.message if body else ""
    )
    await db.commit()
    return ok(result)


@router.get("/projects/{project_id}/join-requests")
async def list_join_requests(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.require_verified_caller()
    return ok(await JoinLinkService(db).list_pending(project_id, actor))


@router.post("/projects/{project_id}/join-requests/{request_id}/approve")
async def approve_join_request(
    project_id: uuid.UUID,
    request_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    actor = await resolver.require_verified_caller()
    await JoinLinkService(db).decide(project_id, request_id, approve=True, actor=actor)
    await db.commit()
    return ok({"approved": True})


@router.post("/projects/{project_id}/join-requests/{request_id}/reject")
async def reject_join_request(
    project_id: uuid.UUID,
    request_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    actor = await resolver.require_verified_caller()
    await JoinLinkService(db).decide(project_id, request_id, approve=False, actor=actor)
    await db.commit()
    return ok({"rejected": True})


@router.post("/projects/{project_id}/members")
async def add_member(
    project_id: uuid.UUID,
    body: MemberCreate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve(fallback_handle=None, project_id=project_id)
    member = await MemberService(db).add(
        project_id=project_id, user_handle=body.user_handle, role=body.role, actor=who
    )
    return ok(MemberOut.model_validate(member).model_dump(mode="json"))


@router.get("/projects/{project_id}/members")
async def list_members(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The project's roster — 队友也在上面。

    「这个项目里有谁」只有一个读法（``membership.roster.roster``），人和 agent 是它
    的两个来源。以前这里只读人那一半，于是一个 agent 在项目里列不出另一个 agent。

    读名册要凭据，和话题列表同一道 项目成员 门：以前它不认凭据就全量作答，一个不是
    成员的人打开空工作区，照样看得见所有人的脸。"""
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    members, _ = await MemberService(db).list_for_project(project_id)
    # 走名册，不走成员行：名册更宽（所有者、小队带进来的人、这个项目的队友都没有成员
    # 行），而且已经是界面该显示的顺序。有成员行的那些，由成员行补上只有它有的字段
    # （id、created_at、存下来的角色）。
    row_of = {m.user_handle: m for m in members}
    items = []
    for member in await roster(db, project_id):
        row = row_of.get(member.handle)
        if row is not None:
            d = MemberOut.model_validate(row).model_dump(mode="json")
            d["name"] = member.name
            d["avatar_id"] = member.avatar_id
        else:
            d = {
                **member.as_dict(),
                "id": None,
                "project_id": str(project_id),
                "user_handle": member.handle,
            }
        d["agent"] = member.agent
        d["active"] = member.active
        d["project_default"] = member.project_default
        items.append(d)
    return ok(page(items, len(items)))


@router.put("/projects/{project_id}/members/{user_handle}")
async def update_member_role(
    project_id: uuid.UUID,
    user_handle: str,
    body: MemberRoleUpdate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve(fallback_handle=None, project_id=project_id)
    member = await MemberService(db).update_role(
        project_id=project_id, user_handle=user_handle, role=body.role, actor=who
    )
    return ok(MemberOut.model_validate(member).model_dump(mode="json"))


@router.delete("/projects/{project_id}/members/{user_handle}")
async def remove_member(
    project_id: uuid.UUID,
    user_handle: str,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await MemberService(db).remove(
        project_id=project_id, user_handle=user_handle, actor=who
    )
    return ok({"deleted": True})


@router.delete("/projects/{project_id}/membership")
async def leave_project(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """退出项目 —— 成员自己走，不是被谁移出。

    路径是 ``/membership`` 而不是 ``/members/me``：``/members/{user_handle}`` 注册在
    上面，``me`` 到了那里就是一个 handle，会被当成「把 me 这个人移出项目」。身份照
    旧只从 resolver 来，退的恒是动作人自己 —— 代退没有入口，也不接受任何自称。
    """
    who = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await MemberService(db).leave(project_id=project_id, actor=who)
    return ok({"deleted": True})


# ---- 邀请：加人这件事需要两个人同意 ------------------------------------------
# 直接写名册的那条路（上面的 POST /members）仍然在，它是这里踩着的地基——接受之后
# 就是它把人放上去的——也是脚本和测试用的原语。差别在于**谁做的决定**：进了项目
# 就看得见这个项目的全部话题，那是别人的工作内容，所以从界面上加人得由被加的那个
# 人点头。


@router.post("/projects/{project_id}/invitations")
async def invite_member(
    project_id: uuid.UUID,
    body: InvitationCreate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve(fallback_handle=None, project_id=project_id)
    invitation = await InvitationService(db).invite(
        project_id=project_id,
        invitee_handle=body.user_handle,
        role=body.role,
        actor=who,
    )
    await db.commit()
    return ok(InvitationOut.model_validate(invitation).model_dump(mode="json"))


@router.get("/projects/{project_id}/invitations")
async def list_project_invitations(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """这个项目还在等谁答复。

    Same door as the roster next door. It lists handles and roles of people
    who have not even joined yet, so "读是开放的" stopped being true the day
    the roster was guarded - this route was left behind holding the old
    promise.
    """
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    svc = InvitationService(db)
    items = await svc.list_for_project(project_id)
    rows = [await svc.describe(i) for i in items]
    return ok(page(rows, len(rows)))


@router.get("/me/invitations")
async def list_my_invitations(db: DbSession, resolver: ActorResolverDep) -> dict:
    """等我答复的邀请。

    没有 project 这一层：被邀请的人还不在那个项目里，一个项目作用域的接口他根本
    够不着——这也是为什么这一条挂在 /me 下面。
    """
    # 必须是**验证过的**身份：这条接口回的是「谁在等你答复」，匿名请求下 resolve
    # 会给出一个 anonymous 演员，那就成了一条不认人的邮箱。
    who = await resolver.require_verified_caller()
    svc = InvitationService(db)
    items = await svc.list_for_person(who.handle or "")
    rows = [await svc.describe(i) for i in items]
    return ok(page(rows, len(rows)))


@router.post("/invitations/{invitation_id}/respond")
async def respond_to_invitation(
    invitation_id: uuid.UUID,
    body: InvitationRespond,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.require_verified_caller()
    invitation = await InvitationService(db).respond(
        invitation_id=invitation_id, accept=body.accept, actor=who
    )
    await db.commit()
    return ok(InvitationOut.model_validate(invitation).model_dump(mode="json"))


@router.delete("/invitations/{invitation_id}")
async def revoke_invitation(
    invitation_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """邀请方反悔。撤回不删行——谁在什么时候邀过谁，是这一行唯一的记录。"""
    who = await resolver.require_verified_caller()
    invitation = await InvitationService(db).revoke(
        invitation_id=invitation_id, actor=who
    )
    await db.commit()
    return ok(InvitationOut.model_validate(invitation).model_dump(mode="json"))
