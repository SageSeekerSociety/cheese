"""What a person's 芝士 does that others see, written out for its gate.

Each kind of action is written out here (`assistant.gate`): what the gate
reads, the calls that do it — the routes the person's own pages call, made as
the person — the routes and places a pass for them opens, and, when the gate
asks first, the card the person confirms instead. ``routes.assistant_agent``
takes the action, asks the gate and hands back the pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from app.api.awaiting_asks import MembershipAsk, RunAsk
from app.api.awaiting_deps import all_waiting
from app.core.errors import BadRequestError, NotFoundError
from app.core.sentences import say
from app.domain.assistant import gate, refs
from app.domain.room_task.awaiting import WaitingItem
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

MESSAGE = "app.api.routes.topics_messages.send_chat_message"
DOORS = {
    "task": "app.api.routes.topics_tasks.create_task",
    "accept": "app.api.routes.accept.accept_card",
    "reject": "app.api.routes.accept.reject_card",
    "alert": "app.api.routes.alerts.resolve_notification",
    "invite": "app.api.routes.members.respond_to_invitation",
    "transfer": "app.api.routes.members.respond_to_transfer",
    "run": "app.api.routes.local_mcp.decide",
    "project_invite": "app.api.routes.members.invite_member",
    "project_remove": "app.api.routes.members.remove_member",
    "project_role": "app.api.routes.members.set_member_role",
    "channel_add": "app.api.routes.topic_members.add_topic_member",
    "channel_remove": "app.api.routes.topic_members.remove_topic_member",
    "channel": "app.api.routes.topics.create_topic",
    "fetch": "app.api.routes.fetch.read_url",
    "team_invite_accept": "app.api.routes.users_team.accept_team_invitation",
    "team_invite_decline": "app.api.routes.users_team.decline_team_invitation",
    "team_join_approve": "app.api.routes.teams.approve_team_join_request",
    "team_join_reject": "app.api.routes.teams.reject_team_join_request",
}
#: How a role is named to the person.
ROLE = {"owner": "所有者", "admin": "管理员", "member": "成员", "mentor": "导师"}
#: Roles from the highest: a move down the list lowers someone.
RANK = ("owner", "admin", "member", "mentor")


@dataclass
class Plan:
    """An action written out: what the gate reads, the calls that do it, the
    routes and places those calls go through, and the card the person
    confirms if the gate asks first (None: asked means not done)."""

    action: gate.Action
    calls: list[dict]
    doors: list[tuple[str, str | None]]
    card: tuple[str, dict] | None = None
    #: What 芝士 is told once the calls are made.
    done: str = ""


class ActIn(BaseModel):
    kind: Literal[
        "message",
        "task",
        "review",
        "respond",
        "member",
        "channel",
        "fetch",
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
            [(DOORS["alert"], None)],
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
            [(DOORS["run"], None)],
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
        [(DOORS[ref.kind], None)],
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
        door = "team_invite_accept" if yes else "team_invite_decline"
        path = f"/users/me/team-invitations/{ask.id}/{'accept' if yes else 'decline'}"
        place = ask.id
        summary = f"{verb} {ask.person.name}的邀请，加入团队「{ask.place.name}」"
        done = f"已{verb}加入团队「{ask.place.name}」的邀请。"
    else:
        verb = "批准" if yes else "拒绝"
        door = "team_join_approve" if yes else "team_join_reject"
        path = (
            f"/teams/{ask.place_id}/join-requests/{ask.id}/"
            f"{'approve' if yes else 'reject'}"
        )
        place = ask.place_id
        summary = f"{verb} {ask.person.name}加入团队「{ask.place.name}」的申请"
        done = f"已{verb} {ask.person.name}加入团队「{ask.place.name}」的申请。"
    return Plan(
        gate.Action("respond", summary),
        [{"method": "POST", "path": path}],
        [(DOORS[door], place)],
        card=("act", {}),
        done=done,
    )
