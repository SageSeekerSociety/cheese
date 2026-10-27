"""What a person's 芝士 does that others see, written out.

Each kind of action is written out here: what its gate reads
(`assistant.gate`), the calls that do it — the routes the person's own pages
call, made as the person — and, when the gate asks first, the card the person
confirms instead. ``routes.assistant_agent`` answers the two questions 芝士's
harness asks of one: whether to take it (``/review``), and how (``/plan``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from app.api.awaiting_asks import MembershipAsk, RunAsk
from app.api.awaiting_deps import all_waiting
from app.core.errors import BadRequestError, NotFoundError
from app.core.sentences import say
from app.domain.agent.personal import reading, space_desk
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.assistant import gate, refs
from app.domain.project import address
from app.domain.room_task.awaiting import WaitingItem
from app.domain.space import permissions as space_seats
from app.domain.space.services import SpaceLabels
from app.domain.team.services import team_service
from app.domain.user.services import search_accounts, user_by_handle


class Person(BaseModel):
    user_id: int
    handle: str
    timezone: str | None


#: Why a membership ask waits on the person, as their inbox page says it.
ASK = {
    "project_invite": "邀请你加入项目",
    "project_transfer": "把项目转让给你",
    "team_invite": "邀请你加入团队",
    "join_request": "申请加入团队",
}

#: How a role is named to the person.
ROLE = {"owner": "所有者", "admin": "管理员", "member": "成员", "mentor": "导师"}
#: Roles from the highest: a move down the list lowers someone.
RANK = ("owner", "admin", "member", "mentor")


@dataclass
class Plan:
    """An action written out: what the gate reads, the calls that do it, and
    the card the person confirms if the gate asks first (None: asked means not
    done)."""

    action: gate.Action
    calls: list[dict]
    card: tuple[str, dict] | None = None
    #: What 芝士 is told once the calls are made.
    done: str = ""
    #: Where it acts, the way the person knows it (「频道「实验」」): what its
    #: step shows. Empty where the step's own arguments say it well enough.
    subject: str = ""


class ActIn(BaseModel):
    kind: Literal[
        "message",
        "task",
        "review",
        "respond",
        "member",
        "channel",
        "fetch",
        "project_set",
        "space_set",
        "announce",
        "category",
        "invite_code",
        "problem_review",
    ]
    #: A message: where (a channel or a task) and what. A task: in which
    #: channel, its title and what it is for (``text``).
    where: str = ""
    text: str = ""
    title: str = ""
    #: A review: its card's ref, ``accept`` or ``reject``, and why (``text``).
    #: An inbox item: its ref and the answer (``choice``).
    ref: str = ""
    choice: str = ""
    #: A member: who (``who``), and ``role`` to give or ``remove``.
    who: str = ""
    role: str = ""
    remove: bool = False
    #: A channel: whether only its members see it.
    members_only: bool = False
    #: A web page, and what to learn from it.
    url: str = ""
    prompt: str = ""
    #: A setting: which one (``key``) and what it becomes (``value``).
    key: str = ""
    value: str = ""
    #: An announcement kept at the top of its space.
    pinned: bool | None = None
    #: A category put away, or brought back.
    archived: bool | None = None
    #: An invite code: how many may use it, and how many days it lasts.
    max_uses: int | None = None
    days: int | None = None
    #: Put it to the person if the gate stops it, rather than hand it back:
    #: 芝士 has nothing to put right, only the person's yes is missing.
    confirm: bool = False


def need(value: str, what: str) -> str:
    if not value.strip():
        raise BadRequestError(say("assistantActNeeds", what=what))
    return value.strip()


async def respond(db, body: ActIn, me: Person) -> Plan:
    ref = refs.parse(need(body.ref, "ref"))
    choice = need(body.choice, "choice")
    if ref is None or ref.kind not in ("alert", "run", *_ASKED):
        raise BadRequestError(say("assistantCannotAnswerHere"))
    waiting = await all_waiting(db, handle=me.handle, user_id=me.user_id)
    if ref.kind == "alert":
        item = next(
            (
                i
                for i in waiting
                if isinstance(i, WaitingItem) and str(i.alert_id) == ref.id
            ),
            None,
        )
        if item is None:
            raise NotFoundError.for_resource("alert", ref.id or "")
        if item.options and choice not in item.options:
            raise BadRequestError(
                say("assistantActNeeds", what=" / ".join(item.options))
            )
        return Plan(
            gate.Action(
                "respond",
                f"答复项目「{item.project_name}」的提醒「{item.headline}」："
                f"选择「{choice}」",
            ),
            [
                {
                    "method": "POST",
                    "path": f"/alerts/{ref.id}/resolve",
                    "body": {"chosen": choice},
                }
            ],
            card=("act", {}),
            done=f"已答复「{item.headline}」：{choice}。",
        )
    if choice not in ("accept", "decline", "allow", "refuse"):
        raise BadRequestError(say("assistantActNeeds", what="choice: accept / decline"))
    yes = choice in ("accept", "allow")
    if ref.kind == "run":
        run = next(
            (i for i in waiting if isinstance(i, RunAsk) and i.id == ref.id), None
        )
        if run is None:
            raise NotFoundError.for_resource("run", ref.id or "")
        return Plan(
            gate.Action(
                "respond",
                f"{'允许' if yes else '拒绝'}项目「{run.project_name}」在你的设备 "
                f"{run.device} 上运行 MCP 服务器 {run.server}（命令：{run.command}）",
                # Refusing is undone by asking again; allowing runs a command
                # on the person's own machine.
                low=not yes,
                always_ask="在你的设备上运行命令总要你确认" if yes else "",
            ),
            [
                {
                    "method": "POST",
                    "path": f"/mcp/approvals/{ref.id}",
                    "body": {"allow": yes},
                }
            ],
            card=("act", {}),
            done=f"已{'允许' if yes else '拒绝'}运行 {run.server}。",
        )
    ask = next(
        (
            i
            for i in waiting
            if isinstance(i, MembershipAsk)
            and i.id == ref.id
            and i.kind == _ASKED[ref.kind]
            and (ref.within is None or i.place_id == ref.within)
        ),
        None,
    )
    if ask is None:
        raise NotFoundError.for_resource(ref.kind, ref.id or "")
    if ref.kind in ("team-invite", "join-request"):
        return _team_answer(ask, yes)
    path = "invitations" if ref.kind == "invite" else "transfers"
    return Plan(
        gate.Action(
            "respond",
            f"{'接受' if yes else '拒绝'} {ask.person.name}{ASK[ask.kind]}"
            f"「{ask.place.name}」",
        ),
        [
            {
                "method": "POST",
                "path": f"/{path}/{ref.id}/respond",
                "body": {"accept": yes},
            }
        ],
        card=("act", {}),
        done=f"已{'接受' if yes else '拒绝'}「{ask.place.name}」的{ASK[ask.kind][-4:]}。",
    )


async def named(db, said: str) -> tuple[str, str]:
    """The handle of the person ``said`` names, and how actions name them
    (「赵一鸣（zhaoym）」, so the check can match it to what the person said):
    a handle as it is, or a name one account alone goes by. Two people by
    that name are not guessed between; 芝士 is told both, to ask which."""
    found = {h: name for h, name in await search_accounts(db, said, 20)}
    if await user_by_handle(db, said) is not None:
        name = found.get(said) or said
        return said, said if name == said else f"{name}（{said}）"
    named = [h for h, name in found.items() if name == said]
    if len(named) == 1:
        return named[0], f"{said}（{named[0]}）"
    if named:
        raise BadRequestError(
            say("assistantWhoAmbiguous", who=said, handles="、".join(named))
        )
    raise NotFoundError(say("assistantWhoUnknown", who=said))


#: The inbox's membership asks, by the kind of ref that answers them.
_ASKED = {
    "invite": "project_invite",
    "transfer": "project_transfer",
    "team-invite": "team_invite",
    "join-request": "join_request",
}


def _team_answer(ask: MembershipAsk, yes: bool) -> Plan:
    """An invitation into a team, or someone asking to join a team the person
    runs, answered as the inbox page answers it."""
    verb = "接受" if yes else "拒绝"
    if ask.kind == "team_invite":
        path = f"/users/me/team-invitations/{ask.id}/{'accept' if yes else 'decline'}"
        summary = f"{verb} {ask.person.name}的邀请，加入团队「{ask.place.name}」"
        done = f"已{verb}加入团队「{ask.place.name}」的邀请。"
    else:
        verb = "批准" if yes else "拒绝"
        path = (
            f"/teams/{ask.place_id}/join-requests/{ask.id}/"
            f"{'approve' if yes else 'reject'}"
        )
        summary = f"{verb} {ask.person.name}加入团队「{ask.place.name}」的申请"
        done = f"已{verb} {ask.person.name}加入团队「{ask.place.name}」的申请。"
    return Plan(
        gate.Action("respond", summary),
        [{"method": "POST", "path": path}],
        card=("act", {}),
        done=done,
    )


#: How a team role is named to the person, by the route's role name.
_TEAM_ROLE = {"OWNER": "所有者", "ADMIN": "管理员", "MEMBER": "成员"}


async def team_member(db, team_id: int, body: ActIn, who: str, shown: str) -> Plan:
    """Someone into a team (an invitation they accept), their role in it
    changed, or them taken out of it — as the team's members page does."""
    team = await team_service(db).get_team(team_id)
    person = await user_by_handle(db, who)
    if team is None:
        raise NotFoundError.for_resource("team", str(team_id))
    if person is None:
        raise NotFoundError(say("assistantWhoUnknown", who=who))
    held = next(
        (
            r
            for r in await team_service(db).get_team_members(team_id)
            if r.user_id == person.id
        ),
        None,
    )
    tid = str(team_id)
    if body.remove:
        return Plan(
            gate.Action(
                "member", f"将 {shown} 移出团队「{team.name}」", ("remove_member",)
            ),
            [{"method": "DELETE", "path": f"/teams/{tid}/members/{person.id}"}],
            card=("act", {}),
            done=f"已把 {shown} 移出团队「{team.name}」。",
        )
    role = (body.role or "member").upper()
    if role not in ("ADMIN", "MEMBER"):
        raise BadRequestError(say("assistantActNeeds", what="role: admin / member"))
    if held is None:
        return Plan(
            gate.Action(
                "member",
                f"邀请 {shown} 以{_TEAM_ROLE[role]}身份加入团队「{team.name}」",
            ),
            [
                {
                    "method": "POST",
                    "path": f"/teams/{tid}/invitations",
                    "body": {"userId": person.id, "role": role},
                }
            ],
            card=("act", {}),
            done=f"已邀请 {shown} 加入团队「{team.name}」，等对方接受。",
        )
    now = {0: "OWNER", 1: "ADMIN", 2: "MEMBER"}.get(held.role, "MEMBER")
    lowers = (now, role) in (
        ("OWNER", "ADMIN"),
        ("OWNER", "MEMBER"),
        ("ADMIN", "MEMBER"),
    )
    return Plan(
        gate.Action(
            "member",
            f"将 {shown} 在团队「{team.name}」的角色从{_TEAM_ROLE[now]}改为"
            f"{_TEAM_ROLE[role]}",
            ("lower_role",) if lowers else (),
        ),
        [
            {
                "method": "PATCH",
                "path": f"/teams/{tid}/members/{person.id}",
                "body": {"role": role},
            }
        ],
        card=("act", {}),
        done=f"已把 {shown} 在团队「{team.name}」改成{_TEAM_ROLE[role]}。",
    )


async def space_member(db, space_id: int, body: ActIn, who: str, shown: str) -> Plan:
    """Someone added to a space or taken out of it, made one of its managers
    or no longer one — as the space's members and settings pages do."""
    space = (await SpaceLabels(db).describe([space_id])).get(space_id)
    person = await user_by_handle(db, who)
    if space is None:
        raise NotFoundError.for_resource("space", str(space_id))
    if person is None:
        raise NotFoundError(say("assistantWhoUnknown", who=who))
    sid = str(space_id)
    manages = await space_seats.runs(db, space_id, person.id)
    if body.remove:
        return Plan(
            gate.Action(
                "member", f"将 {shown} 移出空间「{space.name}」", ("remove_member",)
            ),
            [{"method": "DELETE", "path": f"/spaces/{sid}/members/{person.id}"}],
            card=("act", {}),
            done=f"已把 {shown} 移出空间「{space.name}」。",
        )
    role = (body.role or "member").lower()
    if role not in ("admin", "member"):
        raise BadRequestError(say("assistantActNeeds", what="role: admin / member"))
    if role == "admin":
        return Plan(
            gate.Action("member", f"设 {shown} 为空间「{space.name}」的管理员"),
            [
                {
                    "method": "POST",
                    "path": f"/spaces/{sid}/managers",
                    "body": {"userId": person.id, "role": "ADMIN"},
                }
            ],
            card=("act", {}),
            done=f"已设 {shown} 为空间「{space.name}」的管理员。",
        )
    if manages:
        return Plan(
            gate.Action(
                "member",
                f"撤掉 {shown} 在空间「{space.name}」的管理员身份",
                ("lower_role",),
            ),
            [{"method": "DELETE", "path": f"/spaces/{sid}/managers/{person.id}"}],
            card=("act", {}),
            done=f"已撤掉 {shown} 在空间「{space.name}」的管理员身份。",
        )
    return Plan(
        gate.Action("member", f"把 {shown} 加入空间「{space.name}」"),
        [
            {
                "method": "POST",
                "path": f"/spaces/{sid}/members",
                "body": {"userId": person.id},
            }
        ],
        card=("act", {}),
        done=f"已把 {shown} 加入空间「{space.name}」。",
    )


def _space_id(where: str) -> int:
    ref = refs.parse(need(where, "where"))
    if ref is None or ref.kind != "space" or not (ref.id or "").isdigit():
        raise BadRequestError(say("assistantRefUnknown", ref=where))
    return int(ref.id or 0)


def _id(raw: str) -> int:
    if not raw.strip().isdigit():
        raise BadRequestError(say("assistantRefUnknown", ref=raw))
    return int(raw)


#: What of a space's own settings 芝士 changes: its name for the person, and
#: the field the space's settings page sends.
_SPACE_KEYS = {
    "name": ("名称", "name"),
    "intro": ("简介", "intro"),
    "visible_limit": ("每位出题人可见的题目上限", "visibleTaskLimit"),
}


async def space_set(db, me: Person, body: ActIn) -> Plan:
    sid = _space_id(body.where)
    space = await space_desk.managed(db, me.user_id, sid)
    if body.key not in _SPACE_KEYS:
        raise BadRequestError(
            say("assistantActNeeds", what="key: " + " / ".join(_SPACE_KEYS))
        )
    label, field = _SPACE_KEYS[body.key]
    value: object = need(body.value, "value")
    if body.key == "visible_limit":
        value = _id(str(value))
    return Plan(
        gate.Action("settings", f"把空间「{space.name}」的{label}改为「{value}」"),
        [{"method": "PATCH", "path": f"/spaces/{sid}", "body": {field: value}}],
        card=("act", {}),
        done=f"已把空间「{space.name}」的{label}改为「{value}」。",
    )


async def announce(db, me: Person, body: ActIn) -> Plan:
    """An announcement published to everyone in a space, changed, or taken
    down."""
    sid = _space_id(body.where)
    space = await space_desk.managed(db, me.user_id, sid)
    base = f"/spaces/{sid}/announcements"
    if body.ref:
        aid = _id(body.ref)
        title = await space_desk.announcement_title(db, sid, aid)
        if body.remove:
            return Plan(
                gate.Action(
                    "announce",
                    f"删除空间「{space.name}」的公告「{title}」",
                    ("delete",),
                ),
                [{"method": "DELETE", "path": f"{base}/{aid}"}],
                card=("act", {}),
                done=f"已删除公告「{title}」。",
            )
        change = {
            k: v
            for k, v in (
                ("title", body.title or None),
                ("content", body.text or None),
                ("pinned", body.pinned),
            )
            if v is not None
        }
        if not change:
            raise BadRequestError(
                say("assistantActNeeds", what="title / content / pinned")
            )
        said = "，".join(
            {
                "title": f"标题改为「{body.title}」",
                "content": f"正文改为：{body.text}",
                "pinned": "置顶" if body.pinned else "取消置顶",
            }[k]
            for k in change
        )
        return Plan(
            gate.Action(
                "announce", f"修改空间「{space.name}」的公告「{title}」：{said}"
            ),
            [{"method": "PATCH", "path": f"{base}/{aid}", "body": change}],
            card=("act", {}),
            done=f"已修改公告「{title}」。",
        )
    title = need(body.title, "title")
    return Plan(
        gate.Action(
            "announce",
            f"在空间「{space.name}」发布公告「{title}」，发给空间里所有人"
            + ("，置顶" if body.pinned else "")
            + (f"：{body.text}" if body.text else ""),
            ("to_everyone",),
        ),
        [
            {
                "method": "POST",
                "path": base,
                "body": {
                    "title": title,
                    "content": body.text,
                    "pinned": bool(body.pinned),
                },
            }
        ],
        card=("act", {}),
        done=f"已在空间「{space.name}」发布公告「{title}」。",
    )


async def category(db, me: Person, body: ActIn) -> Plan:
    """A category of a space: made, renamed, put away or brought back, or
    deleted."""
    sid = _space_id(body.where)
    space = await space_desk.managed(db, me.user_id, sid)
    base = f"/spaces/{sid}/categories"
    if not body.ref:
        name = need(body.title, "name")
        return Plan(
            gate.Action(
                "category", f"在空间「{space.name}」新建分类「{name}」", low=True
            ),
            [
                {
                    "method": "POST",
                    "path": base,
                    "body": {"name": name, "description": body.text or None},
                }
            ],
            card=("act", {}),
            done=f"已新建分类「{name}」。",
        )
    cid = _id(body.ref)
    name = await space_desk.category_name(db, sid, cid)
    if body.remove:
        return Plan(
            gate.Action(
                "category", f"删除空间「{space.name}」的分类「{name}」", ("delete",)
            ),
            [{"method": "DELETE", "path": f"{base}/{cid}"}],
            card=("act", {}),
            done=f"已删除分类「{name}」。",
        )
    if body.archived is not None:
        verb = "归档" if body.archived else "取消归档"
        return Plan(
            gate.Action("category", f"{verb}空间「{space.name}」的分类「{name}」"),
            [
                {
                    "method": "POST" if body.archived else "DELETE",
                    "path": f"{base}/{cid}/archive",
                }
            ],
            card=("act", {}),
            done=f"已{verb}分类「{name}」。",
        )
    title = need(body.title, "name")
    return Plan(
        gate.Action(
            "category", f"把空间「{space.name}」的分类「{name}」改名为「{title}」"
        ),
        [{"method": "PATCH", "path": f"{base}/{cid}", "body": {"name": title}}],
        card=("act", {}),
        done=f"已把分类「{name}」改名为「{title}」。",
    )


def _expires(days: int | None) -> int | None:
    if days is None:
        return None
    return int((datetime.now(UTC) + timedelta(days=days)).timestamp() * 1000)


async def invite_code(db, me: Person, body: ActIn) -> Plan:
    """An invite code of a space: made, changed, or stopped."""
    sid = _space_id(body.where)
    space = await space_desk.managed(db, me.user_id, sid)
    base = f"/spaces/{sid}/invite-codes"
    terms = "，".join(
        t
        for t in (
            f"可用 {body.max_uses} 次" if body.max_uses else "",
            f"{body.days} 天后过期" if body.days else "",
            f"说明：{body.text}" if body.text else "",
        )
        if t
    )
    change = {
        k: v
        for k, v in (
            ("maxUses", body.max_uses),
            ("expiresAt", _expires(body.days)),
            ("note", body.text or None),
        )
        if v is not None
    }
    if not body.ref:
        return Plan(
            gate.Action(
                "invite_code",
                f"为空间「{space.name}」新建一个邀请码"
                + (f"（{terms}）" if terms else ""),
            ),
            [{"method": "POST", "path": base, "body": change}],
            card=("act", {}),
            done=f"已为空间「{space.name}」新建邀请码。",
        )
    cid = _id(body.ref)
    code = await space_desk.code_text(db, sid, cid)
    if body.remove:
        return Plan(
            gate.Action(
                "invite_code",
                f"停用空间「{space.name}」的邀请码 {code}",
                ("delete",),
            ),
            [{"method": "DELETE", "path": f"{base}/{cid}"}],
            card=("act", {}),
            done=f"已停用邀请码 {code}。",
        )
    if not change:
        raise BadRequestError(say("assistantActNeeds", what="max_uses / days / note"))
    return Plan(
        gate.Action("invite_code", f"修改空间「{space.name}」的邀请码 {code}：{terms}"),
        [{"method": "PATCH", "path": f"{base}/{cid}", "body": change}],
        card=("act", {}),
        done=f"已修改邀请码 {code}。",
    )


async def problem_review(db, me: Person, body: ActIn) -> Plan:
    """A problem waiting for review on a space the person manages, passed or
    sent back with why."""
    ref = refs.parse(need(body.ref, "ref"))
    if ref is None or ref.kind != "problem" or not ref.within:
        raise BadRequestError(say("assistantRefUnknown", ref=body.ref))
    sid, tid = int(ref.within), int(ref.id or 0)
    space = await space_desk.managed(db, me.user_id, sid)
    name, setter = await space_desk.problem(db, sid, tid)
    if body.choice not in ("accept", "reject"):
        raise BadRequestError(say("assistantActNeeds", what="choice: accept / reject"))
    yes = body.choice == "accept"
    why = body.text.strip()
    if not yes and not why:
        raise BadRequestError(say("assistantActNeeds", what="reason"))
    return Plan(
        gate.Action(
            "problem_review",
            f"{'通过' if yes else '退回'} {setter} 在空间「{space.name}」提交的题目「{name}」"
            + (f"，理由：{why}" if why else ""),
        ),
        [
            {
                "method": "PATCH",
                "path": f"/tasks/{tid}",
                "body": {"approved": "APPROVED"}
                if yes
                else {"approved": "DISAPPROVED", "rejectReason": why},
            }
        ],
        card=("act", {}),
        done=f"已{'通过' if yes else '退回'}题目「{name}」。",
    )


#: What of a project's settings 芝士 changes, by name: the person's word for
#: it, the route's door and path under the project, and the body it sends.
_PROJECT_KEYS = ("slug", "task_naming", "default_agent", "default_model")
_NAMING = {"auto": "自动命名", "manual": "手动命名"}


async def project_set(db, access, body: ActIn) -> Plan:
    """A project's address, how its tasks are named, the teammate and model
    new channels start with; or who manages one of its channels."""
    ref = refs.parse(need(body.where, "where"))
    if ref is not None and ref.kind == "channel":
        if body.key != "manager":
            raise BadRequestError(say("assistantActNeeds", what="key: manager"))
        project, found = await reading.numbered(db, ref, address.Numbered.channel)
        place = await access.topic(found.id)
        who, shown = await named(db, need(body.value, "value").lstrip("@"))
        room = place.room
        return Plan(
            gate.Action(
                "settings",
                f"把频道「{room.title}」（项目「{project.name}」）的负责人换成 {shown}",
            ),
            [
                {
                    "method": "PUT",
                    "path": f"/projects/{project.id}/channels/{room.id}/manager",
                    "body": {"handle": who},
                }
            ],
            card=("act", {}),
            done=f"已把频道「{room.title}」的负责人换成 {shown}。",
        )
    if ref is None or ref.kind != "project":
        raise BadRequestError(say("assistantRefUnknown", ref=body.where))
    project = await address.project_by_ref(db, ref.slug or "")
    if project is None:
        raise NotFoundError.for_resource("project", ref.slug or "")
    await access.project(project.id)
    if body.key not in _PROJECT_KEYS:
        raise BadRequestError(
            say("assistantActNeeds", what="key: " + " / ".join(_PROJECT_KEYS))
        )
    value = need(body.value, "value")
    pid = str(project.id)
    if body.key == "slug":
        said, path, sent = f"短名改为 {value}", "slug", {"slug": value}
    elif body.key == "task_naming":
        if value not in _NAMING:
            raise BadRequestError(say("assistantActNeeds", what="value: auto / manual"))
        said, path, sent = (
            f"任务命名改为{_NAMING[value]}",
            "task-naming",
            {"mode": value},
        )
    elif body.key == "default_model":
        said, path, sent = f"默认模型改为 {value}", "default-model", {"model": value}
    else:
        agents = await AgentInstanceService(db).list_for_project(project.id)
        agent = next((a for a in agents if a.handle == value.lstrip("@")), None)
        if agent is None:
            raise NotFoundError(say("assistantWhoUnknown", who=value))
        said, path, sent = (
            f"默认 AI 队友改为 {agent.handle}",
            "default-agent",
            {"instance_id": str(agent.id)},
        )
    return Plan(
        gate.Action("settings", f"把项目「{project.name}」的{said}"),
        [{"method": "PUT", "path": f"/projects/{pid}/{path}", "body": sent}],
        card=("act", {}),
        done=f"已把项目「{project.name}」的{said}。",
    )
