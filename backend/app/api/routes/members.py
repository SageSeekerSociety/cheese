"""Project membership routes (nested under /api/projects).

The three write routes decide who is a member of the project, and project
membership is what ``authorize_topic_access`` reads to let someone into every
topic of that project. So the acting identity is resolved at the trust boundary
(``ActorResolverDep``) and the service authorizes it — unlike most 2.0 routes
these do NOT honor a handle passed in the body: a claimed handle is exactly the
forgery this surface must not accept. Reading the roster stays open, as it was.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok, page
from app.core.db import get_db
from app.domain.membership.schemas import (
    InvitationCreate,
    InvitationOut,
    InvitationRespond,
    MemberCreate,
    MemberOut,
    MemberRoleUpdate,
)
from app.domain.membership.services import InvitationService, MemberService

router = APIRouter(prefix="", tags=["members"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


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
async def list_members(project_id: uuid.UUID, db: DbSession) -> dict:
    from app.domain.identity.repositories import AgentBindingRepository
    from app.domain.project.repositories import ProjectRepository
    from app.domain.user.repositories import UserRepository

    members, _ = await MemberService(db).list_for_project(project_id)
    # Attach display names (User.name) so the UI can resolve @名字 → handle, and
    # the profile's avatar_id so the chat panel can render the real avatar
    # instead of a colored initial. Both come off the same roster row; avatar_id
    # is None (→ initial fallback) both for a handle with no fusion profile
    # behind it and for anyone still on the global default avatar, i.e. anyone
    # who never picked one — see ``ProjectRepository.list_members``.
    profiles = {
        m["handle"]: m for m in await ProjectRepository(db).list_members(project_id)
    }
    # Same is-agent derivation as the topic roster: a member is an agent iff it
    # carries an AgentBinding — never a handle-string check. The UI badges and
    # filters on this, and every topic's 分身 acts under its own
    # ``cheese-<topic hex>`` handle, so matching the bare string would mis-label
    # any 分身 that ever lands on a project roster.
    # One query for the whole roster, not one per member: an `await` inside a
    # dict comprehension reads as a batch and is not one — it was 30 round trips
    # on a 30-person project, 10.3 ms of a 32.8 ms response. `get_by_handles`
    # exists for exactly this and says so.
    users = UserRepository(db)
    rows = await users.get_by_handles(list(profiles))
    agent_ids = await AgentBindingRepository(db).agent_user_ids(
        [u.id for u in rows.values()]
    )
    items = []
    for m in members:
        d = MemberOut.model_validate(m).model_dump(mode="json")
        profile = profiles.get(m.user_handle)
        d["name"] = profile["name"] if profile else m.user_handle
        d["avatar_id"] = profile["avatar_id"] if profile else None
        user = rows.get(m.user_handle)
        d["agent"] = user is not None and user.id in agent_ids
        items.append(d)
    for handle, profile in profiles.items():
        if profile.get("source") != "team":
            continue
        user = rows.get(handle)
        items.append(
            {
                **profile,
                "id": None,
                "project_id": str(project_id),
                "user_handle": handle,
                "agent": user is not None and user.id in agent_ids,
            }
        )
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
async def list_project_invitations(project_id: uuid.UUID, db: DbSession) -> dict:
    """这个项目还在等谁答复。读是开放的，和读名册一样。"""
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
