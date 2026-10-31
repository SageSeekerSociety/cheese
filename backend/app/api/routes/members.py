"""Project membership routes (nested under /api/projects).

The write routes decide who is a member of the project, and project membership
is what ``authorize_topic_access`` reads to let someone into every topic of that
project. So the acting identity is resolved at the trust boundary (``Can``,
which judges the project permission a route needs, or ``ActorResolverDep``)
and the service authorizes it — unlike most 2.0 routes
these do NOT honor a handle passed in the body: a claimed handle is exactly the
forgery this surface must not accept. Reading the roster used to stay open; it
does not any more (see ``list_members``). The invitation list is part of the
same surface - it names who is still expected to answer - and took the same
door late, because this docstring still promised the old behaviour.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, page, typed_response
from app.api.write_access import ROUTE_DECIDES, Can, InProject
from app.auth.project_roles import RoleDef, seats, standing
from app.core.errors import NotFoundError
from app.domain.agent_instance.adapters import project_team
from app.domain.delivery.ledger import deliver, settle
from app.domain.membership.roster import roster
from app.domain.membership.schemas import (
    InvitationCreate,
    InvitationOut,
    InvitationRespond,
    MemberCreate,
    MemberOut,
    MovedOut,
    MoveIn,
    ProjectRoleOut,
    ProjectRolesOut,
    RoleCreate,
    RoleDeletedOut,
    RoleOut,
    RoleSet,
    RoleUpdate,
    TransferCreate,
    TransferOut,
)
from app.domain.membership.services import (
    InvitationService,
    MemberService,
    OwnershipService,
    RoleService,
)
from app.domain.project_permissions import (
    DEFAULT_GRANTS,
    PERMISSION_ORDER,
    Permission,
    Template,
)
from app.domain.user.services import remarks_by_handles

router = APIRouter(prefix="", tags=["members"])


def _invitations(db: DbSession) -> InvitationService:
    """The invitation service, with the two ledger writes wired in.

    An invitation is delivered, and settled once it is answered or withdrawn —
    both land on the delivery ledger, one layer above ``membership``, so this
    door hands them in (``integration/ports.py`` wires its reader the same way).
    """
    return InvitationService(db, deliver=deliver, settle=settle, team=project_team)


@router.post("/projects/{project_id}/members")
async def add_member(
    at: Annotated[InProject, Can(Permission.configure_agents)],
    body: MemberCreate,
    db: DbSession,
) -> dict:
    member = await MemberService(db).seat_agent(
        project_id=at.project.id, user_handle=body.user_handle, actor=at.actor
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
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    people = await roster(db, project_id, team=project_team)
    members, _ = await MemberService(db).list_for_project(project_id)
    # The roster is the answer; a stored row adds only its id and when it was
    # written (an external member's acceptance, a teammate's seat).
    row_of = {m.user_handle: m for m in members}
    seated = await seats(db, project_id, [m.handle for m in people if not m.agent])
    viewer_handle = actor.handle if actor.authenticated else None
    remarks = await remarks_by_handles(db, viewer_handle, [m.handle for m in people])
    items = []
    for member in people:
        row = row_of.get(member.handle)
        d = {
            **member.as_dict(),
            "id": str(row.id) if row is not None else None,
            "project_id": str(project_id),
            "user_handle": member.handle,
            "remark": remarks.get(member.handle),
        }
        if not member.agent:
            held = seated.get(member.handle)
            if held is not None:
                # The role they hold, how high that sits, and whether it is
                # the team's to change.
                d["role"] = held.role
                d["seat"] = held.seat.value
                d["role_from_team"] = held.from_team
        if row is not None:
            d["created_at"] = row.created_at.isoformat()
        items.append(d)
    return ok(page(items, len(items)))


@router.delete("/projects/{project_id}/members/{user_handle}")
async def remove_member(
    at: Annotated[InProject, Can(Permission.manage_members)],
    user_handle: str,
    db: DbSession,
) -> dict:
    await MemberService(db).remove(
        project_id=at.project.id, user_handle=user_handle, actor=at.actor
    )
    return ok({"deleted": True})


@router.delete("/projects/{project_id}/membership", dependencies=[ROUTE_DECIDES])
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
    who = await resolver.resolve(project_id=project_id)
    await MemberService(db).leave(project_id=project_id, actor=who)
    return ok({"deleted": True})


# ---- 邀请：加人这件事需要两个人同意 ------------------------------------------
# 直接写名册的那条路（上面的 POST /members）仍然在，它是这里踩着的地基——接受之后
# 就是它把人放上去的——也是脚本和测试用的原语。差别在于**谁做的决定**：进了项目
# 就看得见这个项目的全部话题，那是别人的工作内容，所以从界面上加人得由被加的那个
# 人点头。


@router.post("/projects/{project_id}/invitations")
async def invite_member(
    at: Annotated[InProject, Can(Permission.manage_members)],
    body: InvitationCreate,
    db: DbSession,
) -> dict:
    invitation = await _invitations(db).invite(
        project_id=at.project.id,
        invitee_handle=body.user_handle,
        actor=at.actor,
        role=body.role,
    )
    await db.commit()
    return ok(InvitationOut.model_validate(invitation).model_dump(mode="json"))


@router.get("/projects/{project_id}/invitations")
async def list_project_invitations(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """这个项目还在等谁答复。

    Same door as the roster next door. It lists people who have not even
    joined yet, so "读是开放的" stopped being true the day
    the roster was guarded - this route was left behind holding the old
    promise.
    """
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    svc = _invitations(db)
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
    svc = _invitations(db)
    items = await svc.list_for_person(who.handle or "")
    rows = [await svc.describe(i) for i in items]
    return ok(page(rows, len(rows)))


@router.post("/invitations/{invitation_id}/respond", dependencies=[ROUTE_DECIDES])
async def respond_to_invitation(
    invitation_id: uuid.UUID,
    body: InvitationRespond,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.require_verified_caller()
    invitation = await _invitations(db).respond(
        invitation_id=invitation_id, accept=body.accept, actor=who
    )
    await db.commit()
    return ok(InvitationOut.model_validate(invitation).model_dump(mode="json"))


@router.delete("/invitations/{invitation_id}", dependencies=[ROUTE_DECIDES])
async def revoke_invitation(
    invitation_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """邀请方反悔。撤回不删行——谁在什么时候邀过谁，是这一行唯一的记录。"""
    who = await resolver.require_verified_caller()
    invitation = await _invitations(db).revoke(invitation_id=invitation_id, actor=who)
    await db.commit()
    return ok(InvitationOut.model_validate(invitation).model_dump(mode="json"))


# ---- 角色与权限（#3830、#4795）-------------------------------------------------


@router.put(
    "/projects/{project_id}/members/{user_handle}/role", **typed_response(RoleOut)
)
async def set_member_role(
    at: Annotated[InProject, Can(Permission.change_roles)],
    user_handle: str,
    body: RoleSet,
    db: DbSession,
) -> dict:
    """Give someone below the caller another role."""
    project_id = at.project.id
    await RoleService(db).set_role(
        project_id=project_id, handle=user_handle, role=body.role, actor=at.actor
    )
    held = await standing(db, project_id, user_handle)
    await db.commit()
    return ok({"role": held.role if held is not None else None})


def _ordered(names) -> list[str]:
    return [p.value for p in PERMISSION_ORDER if p in names]


def _role_out(role: RoleDef, holders: dict[str, int]) -> dict:
    return ProjectRoleOut(
        key=role.key,
        name=role.name,
        builtin=role.builtin.value if role.builtin is not None else None,
        template=role.template.value if role.template is not None else None,
        permissions=_ordered(role.permissions),
        defaults=_ordered(DEFAULT_GRANTS[role.builtin])
        if role.builtin is not None
        else None,
        holders=holders.get(role.key, 0),
    ).model_dump(mode="json")


async def _roles_payload(db: DbSession, project_id: uuid.UUID) -> dict:
    service = RoleService(db)
    holders = await service.holders(project_id)
    return {
        "roles": [
            _role_out(role, holders)
            for role in (await service.roles(project_id)).values()
        ]
    }


@router.get("/projects/{project_id}/roles", **typed_response(ProjectRolesOut))
async def list_roles(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The project's roles and what each may do. Everyone in it may read them."""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    return ok(await _roles_payload(db, project_id))


@router.post("/projects/{project_id}/roles", **typed_response(ProjectRoleOut))
async def create_role(
    at: Annotated[InProject, Can(Permission.change_roles)],
    body: RoleCreate,
    db: DbSession,
) -> dict:
    role = await RoleService(db).create(
        project_id=at.project.id,
        name=body.name,
        copy_from=body.copy_from,
        template=Template(body.template) if body.template else None,
        actor=at.actor,
    )
    await db.commit()
    return ok(_role_out(role, {}))


@router.patch("/projects/{project_id}/roles/{key}", **typed_response(ProjectRoleOut))
async def update_role(
    at: Annotated[InProject, Can(Permission.change_roles)],
    key: str,
    body: RoleUpdate,
    db: DbSession,
) -> dict:
    service = RoleService(db)
    role = await service.update(
        project_id=at.project.id,
        key=key,
        name=body.name,
        permissions=body.permissions,
        actor=at.actor,
    )
    holders = await service.holders(at.project.id)
    await db.commit()
    return ok(_role_out(role, holders))


@router.delete("/projects/{project_id}/roles/{key}", **typed_response(RoleDeletedOut))
async def delete_role(
    at: Annotated[InProject, Can(Permission.change_roles)],
    key: str,
    db: DbSession,
    move_to: str | None = None,
) -> dict:
    """Delete one of the project's own roles. Whoever holds it moves to the
    role ``move_to`` names, which is needed only while someone does."""
    await RoleService(db).delete(
        project_id=at.project.id, key=key, move_to=move_to, actor=at.actor
    )
    await db.commit()
    return ok({"deleted": True})


# ---- 转让与移到团队：只有所有者 -----------------------------------------------


def _ownership(db: DbSession) -> OwnershipService:
    return OwnershipService(db, deliver=deliver, settle=settle)


@router.post(
    "/projects/{project_id}/transfer",
    dependencies=[ROUTE_DECIDES],
    **typed_response(TransferOut),
)
async def offer_transfer(
    project_id: uuid.UUID,
    body: TransferCreate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """The owner offers the project to someone in it; nothing changes until
    they accept."""
    who = await resolver.resolve(project_id=project_id)
    offer = await _ownership(db).offer(
        project_id=project_id, to_handle=body.to_handle, actor=who
    )
    await db.commit()
    return ok(TransferOut.model_validate(offer).model_dump(mode="json"))


@router.delete(
    "/projects/{project_id}/transfer",
    dependencies=[ROUTE_DECIDES],
    **typed_response(TransferOut),
)
async def cancel_transfer(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    who = await resolver.resolve(project_id=project_id)
    service = _ownership(db)
    pending = await service.pending_for(project_id)
    if pending is None:
        raise NotFoundError()
    offer = await service.cancel(transfer_id=pending.id, actor=who)
    await db.commit()
    return ok(TransferOut.model_validate(offer).model_dump(mode="json"))


@router.post(
    "/transfers/{transfer_id}/respond",
    dependencies=[ROUTE_DECIDES],
    **typed_response(TransferOut),
)
async def respond_to_transfer(
    transfer_id: uuid.UUID,
    body: InvitationRespond,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.require_verified_caller()
    offer = await _ownership(db).respond(
        transfer_id=transfer_id, accept=body.accept, actor=who
    )
    await db.commit()
    return ok(TransferOut.model_validate(offer).model_dump(mode="json"))


@router.post(
    "/projects/{project_id}/move",
    dependencies=[ROUTE_DECIDES],
    **typed_response(MovedOut),
)
async def move_to_team(
    project_id: uuid.UUID,
    body: MoveIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Move the owner's own project into a team they manage."""
    who = await resolver.resolve(project_id=project_id)
    project = await _ownership(db).move_to_team(
        project_id=project_id, team_id=body.team_id, actor=who
    )
    await db.commit()
    return ok({"project_id": str(project.id), "team_id": project.team_id})
