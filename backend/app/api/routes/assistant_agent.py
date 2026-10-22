"""What a person's 芝士 reads with: the inbox, a thing by its ref, a search.

Each answers in 芝士's own words rather than the page's JSON: what a thing is,
its name, and the ref that reads it further (`assistant.refs`), within a
length the conversation can afford. The script that calls these
(``sandbox/cheese``) passes the text on as it is, so how a thing reads to 芝士
is decided here, once, where the person's permissions are.

Everything is read as the person: the delegated credential's holder may see
what they may see on the site, and nothing their pages would not show them.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.assistant_deps import ProjectPages, show_proposal, tell_change, where_of
from app.api.auth import ActorResolverDep
from app.api.awaiting_asks import MembershipAsk, RunAsk, TaskAsk
from app.api.awaiting_deps import all_waiting
from app.api.deps import get_consumptions
from app.api.request_deps import DbSession
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.auth.project_roles import standing
from app.core.errors import AuthenticationRequiredError, BadRequestError, NotFoundError
from app.core.sentences import say
from app.domain.agent.personal import reading
from app.domain.agent.session_host.consumptions import Consumptions
from app.domain.assistant import changes, gate, passes, proposals, refs
from app.domain.fetch.preapproved import preapproved
from app.domain.notification.preferences import local_time
from app.domain.project import address
from app.domain.project.services import ProjectService
from app.domain.review.services import deliverable_revision
from app.domain.room_task.awaiting import WaitingItem
from app.domain.topic.services import TopicService
from app.domain.user.services import search_accounts, timezones_by_ids, user_by_handle

Questions = Annotated[Consumptions, Depends(get_consumptions)]

router = APIRouter(
    prefix="/assistant/agent", tags=["assistant"], dependencies=[ROUTE_DECIDES]
)

#: The most items the inbox lists at once; it says how many more there are.
INBOX_LIMIT = 30

#: Why a thing waits on the person, as their inbox page says it
#: (``home.inbox.reason``).
_REASON = {
    "reviewer": "待你审阅",
    "reporter": "你提的需求已有交付",
    "asked": "待你回答",
    "checksFailed": "检查未通过",
    "blocked": "卡住了",
    "bounced": "已退回",
    "decide": "待你决定",
    "read": "变更提醒",
}
_ASK = {
    "project_invite": "邀请你加入项目",
    "project_transfer": "把项目转让给你",
    "team_invite": "邀请你加入团队",
    "join_request": "申请加入团队",
}
_ASK_REF = {
    "project_invite": "invites",
    "project_transfer": "transfers",
    "team_invite": "team-invites",
    "join_request": "join-requests",
}


class Person(BaseModel):
    user_id: int
    handle: str
    timezone: str | None


async def _person(resolver, db) -> Person:
    actor = await resolver.resolve()
    if not actor.authenticated or actor.user_id is None:
        raise AuthenticationRequiredError(say("invalidToken"))
    zones = await timezones_by_ids(db, [actor.user_id])
    return Person(
        user_id=actor.user_id,
        handle=actor.handle,
        timezone=zones.get(actor.user_id),
    )


def _when(moment: datetime, timezone: str | None) -> str:
    """A moment on the person's clock, as their pages show it."""
    here = local_time(moment, timezone)
    today = local_time(datetime.now(UTC), timezone).date()
    if here.date() == today:
        return f"今天 {here:%H:%M}"
    if here.year == today.year:
        return f"{here.month}月{here.day}日 {here:%H:%M}"
    return f"{here.year}年{here.month}月{here.day}日"


class _Names:
    """Each project's slug and each numbered thing's number, read once for a
    whole list."""

    def __init__(self, db) -> None:
        self._db = db
        self._slugs: dict[uuid.UUID, str] = {}
        self._numbers: dict[uuid.UUID, int | None] = {}

    async def slug(self, project_id: uuid.UUID) -> str:
        if project_id not in self._slugs:
            project = await ProjectService(self._db).get(project_id)
            self._slugs[project_id] = project.slug if project else str(project_id)
        return self._slugs[project_id]

    async def number(self, kind: address.Numbered, thing_id: uuid.UUID) -> int | None:
        if thing_id not in self._numbers:
            found = await address.thing_by_id(self._db, kind, thing_id)
            self._numbers[thing_id] = found.number if found else None
        return self._numbers[thing_id]


async def _work_line(item: WaitingItem, names: _Names, tz: str | None) -> str:
    slug = await names.slug(item.project_id)
    where = [f"项目「{item.project_name}」（{refs.project(slug)}）"]
    ref = None
    if item.topic_id is not None:
        number = await names.number(address.Numbered.channel, item.topic_id)
        where.append(f"频道「{item.topic_title}」")
        ref = refs.channel(slug, number)
    if item.task_id is not None:
        number = await names.number(address.Numbered.task, item.task_id)
        where.append(f"任务「{item.task_title or '新任务'}」")
        ref = (
            refs.review(slug, number)
            if item.reason == "reviewer"
            else refs.task(slug, number)
        ) or ref
    reason = _REASON.get(item.reason, item.reason)
    lines = [f"- {reason}：{'，'.join(where)}，{_when(item.at, tz)}"]
    if item.alert_id is not None:
        lines.append(f"  提醒 {refs.of('alerts', item.alert_id)}：{item.headline}")
        if item.options:
            lines.append(f"  可选：{' / '.join(item.options)}")
    if item.detail:
        lines.append(f"  {item.detail}")
    if ref:
        lines.append(f"  读：{ref}")
    return "\n".join(lines)


def _ask_line(item: MembershipAsk, tz: str | None) -> str:
    within = item.place_id if item.kind == "join_request" else None
    ref = refs.of(_ASK_REF[item.kind], item.id, within=within)
    role = f"，身份：{_ROLE.get(item.role, item.role)}" if item.role else ""
    said = f"\n  附言：{item.message}" if item.message else ""
    return (
        f"- {item.person.name}{_ASK[item.kind]}「{item.place.name}」{role}，"
        f"{_when(item.at, tz)}\n  答复用：{ref}{said}"
    )


def _run_line(item: RunAsk, tz: str | None) -> str:
    return (
        f"- 待你允许：在你的设备 {item.device} 上运行 MCP 服务器 {item.server}"
        f"（项目「{item.project_name}」），{_when(item.at, tz)}\n"
        f"  命令：{item.command}\n  答复用：{refs.of('runs', item.id)}"
    )


def _teaching_line(item: TaskAsk, tz: str | None) -> str:
    waiting = "，".join(
        part
        for part in (
            f"{item.claims} 份领取待批准" if item.claims else "",
            f"{item.reviews} 份提交待评审" if item.reviews else "",
        )
        if part
    )
    return (
        f"- 题目「{item.task_name}」（空间「{item.space_name}」）：{waiting}，"
        f"{_when(item.at, tz)}\n  读：{refs.problem(item.space_id, item.task_id)}"
    )


class InboxIn(BaseModel):
    #: Only what is in this project (its slug).
    project: str | None = None


@router.post("/inbox", include_in_schema=False)
async def agent_inbox(body: InboxIn, resolver: ActorResolverDep, db: DbSession) -> dict:
    """cheese_inbox: what waits on the person, newest first, as their inbox
    page lists it."""
    me = await _person(resolver, db)
    items = await all_waiting(db, handle=me.handle, user_id=me.user_id)
    if body.project:
        project = await address.project_by_ref(db, body.project)
        wanted = project.id if project else None
        items = [
            i
            for i in items
            if isinstance(i, WaitingItem)
            and i.project_id == wanted
            or isinstance(i, RunAsk)
            and wanted
            and i.project_id == str(wanted)
        ]
    if not items:
        return ok({"text": "现在没有等你处理的事。"})
    names = _Names(db)
    lines = []
    for item in items[:INBOX_LIMIT]:
        if isinstance(item, WaitingItem):
            lines.append(await _work_line(item, names, me.timezone))
        elif isinstance(item, MembershipAsk):
            lines.append(_ask_line(item, me.timezone))
        elif isinstance(item, RunAsk):
            lines.append(_run_line(item, me.timezone))
        else:
            lines.append(_teaching_line(item, me.timezone))
    more = len(items) - INBOX_LIMIT
    head = f"等你处理的事，共 {len(items)} 件，最近的在前："
    tail = (
        f"\n还有 {more} 件更早的没列出；按项目筛（project）可以看到它们。"
        if more > 0
        else ""
    )
    return ok({"text": head + "\n" + "\n".join(lines) + tail})


class ReadIn(BaseModel):
    ref: str
    page: int = 1


@router.post("/read", include_in_schema=False)
async def agent_read(body: ReadIn, resolver: ActorResolverDep, db: DbSession) -> dict:
    """cheese_read for a project's own things: the project, a channel, a task,
    its review card, a document."""
    me = await _person(resolver, db)
    ref = refs.parse(body.ref)
    if ref is not None and ref.kind in _WAITING_KINDS:
        return ok({"text": await _waiting_one(db, me, ref)})
    if ref is None or ref.kind not in reading.KINDS:
        raise BadRequestError(say("assistantRefUnknown", ref=body.ref))
    reader = reading.Reader(session=db, user_id=me.user_id, timezone=me.timezone)
    text = await reading.read(reader, ProjectPages(db, resolver), ref, body.page)
    return ok({"text": text})


#: The refs of what waits on the person, by the kind ``refs.parse`` names:
#: they are read from the inbox, which is where they live.
_WAITING_KINDS = {
    "alert": "alert",
    "invite": "project_invite",
    "transfer": "project_transfer",
    "team-invite": "team_invite",
    "join-request": "join_request",
    "run": "run",
}


async def _waiting_one(db, me: Person, ref) -> str:
    """One thing from the person's inbox, as the inbox lists it; or that it
    no longer waits."""
    wanted, kind = str(ref.id), _WAITING_KINDS[ref.kind]
    for item in await all_waiting(db, handle=me.handle, user_id=me.user_id):
        if kind == "alert" and isinstance(item, WaitingItem):
            if str(item.alert_id) == wanted:
                line = await _work_line(item, _Names(db), me.timezone)
                return line + "\n答复用 cheese_inbox_respond。"
        elif kind == "run" and isinstance(item, RunAsk):
            if str(item.id) == wanted:
                return _run_line(item, me.timezone)
        elif isinstance(item, MembershipAsk) and item.kind == kind:
            if str(item.id) == wanted:
                return _ask_line(item, me.timezone)
    return say(
        "assistantNoLongerWaiting",
        ref=refs.of(ref.kind + "s", ref.id, within=ref.within),
    )


# What 芝士 does that others see (`assistant.gate`). Each kind of action is
# written out here: what the gate reads, the calls that do it — the routes
# the person's own pages call, made as the person — and, when the gate asks
# first, the card the person confirms instead.

_MESSAGE = "app.api.routes.topics_messages.send_chat_message"
_DOORS = {
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
}
#: How a role is named to the person.
_ROLE = {"owner": "所有者", "admin": "管理员", "member": "成员", "mentor": "导师"}
#: Roles from the highest: a move down the list lowers someone.
_RANK = ("owner", "admin", "member", "mentor")


@dataclass
class _Plan:
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


def _need(value: str, what: str) -> str:
    if not value.strip():
        raise BadRequestError(say("assistantActNeeds", what=what))
    return value.strip()


def _place_ref(ref: refs.Ref) -> address.Numbered:
    if ref.kind == "channel":
        return address.Numbered.channel
    if ref.kind == "task":
        return address.Numbered.task
    raise BadRequestError(say("assistantRefUnknown", ref=ref.kind))


async def _place(db, access: ProjectPages, raw: str):
    """The channel or task a ref names, as the person may see it."""
    ref = refs.parse(raw)
    if ref is None:
        raise BadRequestError(say("assistantRefUnknown", ref=raw))
    project, found = await reading.numbered(db, ref, _place_ref(ref))
    return project, await access.topic(found.id), ref


async def _message(db, access, body: ActIn, me: Person) -> _Plan:
    text = _need(body.text, "text")
    project, place, ref = await _place(db, access, _need(body.where, "where"))
    name = where_of(place)
    everyone = (
        place.task is None and place.thread is None and str(place.room.kind) == "root"
    )
    return _Plan(
        gate.Action(
            "message",
            f"在{'任务' if place.task else '频道'}「{name}」（项目「{project.name}」"
            f"{'，全体成员都会看到' if everyone else ''}）发消息：\n{text}",
            ("to_everyone",) if everyone else (),
        ),
        [
            {
                "method": "POST",
                "path": f"/topics/{place.conversation_id}/messages",
                "body": {"content": text, "request_id": str(uuid.uuid4())},
            }
        ],
        [(_MESSAGE, str(place.conversation_id))],
        card=(
            "message",
            {
                "topic": str(place.conversation_id),
                "topicName": name,
                "text": text,
                "project": str(project.id),
            },
        ),
        done=f"已在「{name}」发出。",
    )


async def _task(db, access, body: ActIn, me: Person) -> _Plan:
    title, text = _need(body.title, "title"), _need(body.text, "text")
    project, place, _ = await _place(db, access, _need(body.where, "where"))
    room = place.room
    return _Plan(
        gate.Action(
            "task",
            f"在频道「{room.title}」（项目「{project.name}」）开任务「{title}」，"
            f"交给项目的 AI 队友，交代：\n{text}",
        ),
        [
            {
                "method": "POST",
                "path": f"/topics/{room.id}/tasks",
                "body": {"title": title},
            },
            {
                "method": "POST",
                "path": "/topics/{prev}/messages",
                "body": {"content": text, "request_id": str(uuid.uuid4())},
            },
        ],
        [(_DOORS["task"], str(room.id)), (_MESSAGE, None)],
        card=(
            "task",
            {
                "channel": str(room.id),
                "channelName": room.title,
                "title": title,
                "brief": text,
                "project": str(project.id),
            },
        ),
        done=f"已在频道「{room.title}」开了任务「{title}」，AI 队友会接着做。",
    )


async def _review(db, access, body: ActIn, me: Person) -> _Plan:
    ref = refs.parse(_need(body.ref, "ref"))
    if ref is None or ref.kind not in ("review", "task"):
        raise BadRequestError(say("assistantRefUnknown", ref=body.ref))
    project, found = await reading.numbered(db, ref, address.Numbered.task)
    place = await access.topic(found.id)
    card = await reading.latest_card(db, found.id)
    if card is None:
        raise BadRequestError(say("assistantNoReviewCard"))
    if body.choice == "accept":
        verb, call = (
            "采纳",
            {
                "method": "POST",
                "path": f"/accept-cards/{card.id}/accept",
                # The version 芝士 just read, as a person's card carries it:
                # a file or link replaced since is refused, not accepted.
                "body": {
                    "decided_by": me.handle,
                    "head_sha": card.pr_head_sha,
                    "deliverable_revision": deliverable_revision(project.id, card),
                },
            },
        )
    elif body.choice == "reject":
        note = _need(body.text, "text")
        verb, call = (
            f"退回，说明：{note}",
            {
                "method": "POST",
                "path": f"/accept-cards/{card.id}/reject",
                "body": {"decided_by": me.handle, "note": note},
            },
        )
    else:
        raise BadRequestError(say("assistantActNeeds", what="choice: accept / reject"))
    door = _DOORS["accept" if body.choice == "accept" else "reject"]
    return _Plan(
        gate.Action(
            "review",
            f"{verb.split('，')[0]}任务「{place.task.title}」的审阅卡（项目"
            f"「{project.name}」，改动：{card.change_subject or '未写'}"
            f"{reading.checks_of(card)}）"
            + (f"，{verb.split('，', 1)[1]}" if "，" in verb else ""),
        ),
        [call],
        [(door, None)],
        card=("act", {}),
        done=f"已{verb.split('，')[0]}任务「{place.task.title}」的审阅卡。",
    )


async def _respond(db, body: ActIn, me: Person) -> _Plan:
    ref = refs.parse(_need(body.ref, "ref"))
    choice = _need(body.choice, "choice")
    if ref is None or ref.kind not in ("alert", "invite", "transfer", "run"):
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
        return _Plan(
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
            [(_DOORS["alert"], None)],
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
        return _Plan(
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
            [(_DOORS["run"], None)],
            card=("act", {}),
            done=f"已{'允许' if yes else '拒绝'}运行 {run.server}。",
        )
    ask = next(
        (i for i in waiting if isinstance(i, MembershipAsk) and i.id == ref.id), None
    )
    if ask is None:
        raise NotFoundError.for_resource(ref.kind, ref.id or "")
    path = "invitations" if ref.kind == "invite" else "transfers"
    return _Plan(
        gate.Action(
            "respond",
            f"{'接受' if yes else '拒绝'} {ask.person.name}{_ASK[ask.kind]}"
            f"「{ask.place.name}」",
        ),
        [
            {
                "method": "POST",
                "path": f"/{path}/{ref.id}/respond",
                "body": {"accept": yes},
            }
        ],
        [(_DOORS[ref.kind], None)],
        card=("act", {}),
        done=f"已{'接受' if yes else '拒绝'}「{ask.place.name}」的{_ASK[ask.kind][-4:]}。",
    )


async def _named(db, said: str) -> tuple[str, str]:
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


async def _member(db, access, body: ActIn, me: Person) -> _Plan:
    who, shown = await _named(db, _need(body.who, "who").lstrip("@"))
    ref = refs.parse(_need(body.where, "where"))
    if ref is None:
        raise BadRequestError(say("assistantRefUnknown", ref=body.where))
    if ref.kind == "channel":
        project, place, _ = await _place(db, access, body.where)
        room = place.room
        if body.remove:
            return _Plan(
                gate.Action(
                    "member",
                    f"将 {shown} 移出频道「{room.title}」（项目「{project.name}」）",
                    ("remove_member",),
                ),
                [{"method": "DELETE", "path": f"/topics/{room.id}/members/{who}"}],
                [(_DOORS["channel_remove"], str(room.id))],
                card=("act", {}),
                done=f"已把 {shown} 移出频道「{room.title}」。",
            )
        return _Plan(
            gate.Action(
                "member",
                f"将 {shown} 加入{'私密' if room.members_only else ''}频道"
                f"「{room.title}」（项目「{project.name}」）",
                # An open channel is one they could join themselves; a
                # private one is let into.
                low=not room.members_only,
            ),
            [
                {
                    "method": "POST",
                    "path": f"/topics/{room.id}/members",
                    "body": {"handle": who},
                }
            ],
            [(_DOORS["channel_add"], str(room.id))],
            card=("act", {}),
            done=f"已把 {shown} 加进频道「{room.title}」。",
        )
    if ref.kind != "project":
        raise BadRequestError(say("assistantRefUnknown", ref=body.where))
    project = await address.project_by_ref(db, ref.slug or "")
    if project is None:
        raise NotFoundError.for_resource("project", ref.slug or "")
    await access.project(project.id)
    pid = str(project.id)
    held = await standing(db, project.id, who)
    seat = held.seat.value if held is not None else None
    if body.remove:
        return _Plan(
            gate.Action(
                "member",
                f"将{_ROLE.get(seat or '', '不在项目中的')} {shown} 移出项目「{project.name}」",
                ("remove_member",),
            ),
            [{"method": "DELETE", "path": f"/projects/{pid}/members/{who}"}],
            [(_DOORS["project_remove"], pid)],
            card=("act", {}),
            done=f"已把 {shown} 移出项目「{project.name}」。",
        )
    role = body.role or "member"
    if role not in ("admin", "member", "mentor"):
        raise BadRequestError(
            say("assistantActNeeds", what="role: admin / member / mentor")
        )
    if seat is None:
        if role == "admin":
            raise BadRequestError(say("assistantInviteThenAdmin"))
        return _Plan(
            gate.Action(
                "member",
                f"邀请 {shown} 以{_ROLE[role]}身份加入项目「{project.name}」",
            ),
            [
                {
                    "method": "POST",
                    "path": f"/projects/{pid}/invitations",
                    "body": {"user_handle": who, "role": role},
                }
            ],
            [(_DOORS["project_invite"], pid)],
            card=("act", {}),
            done=f"已邀请 {shown} 加入项目「{project.name}」，等对方接受。",
        )
    lowers = _RANK.index(role) > _RANK.index(seat)
    return _Plan(
        gate.Action(
            "member",
            f"将 {shown} 在项目「{project.name}」的角色从{_ROLE[seat]}改为{_ROLE[role]}",
            ("lower_role",) if lowers else (),
        ),
        [
            {
                "method": "PUT",
                "path": f"/projects/{pid}/members/{who}/role",
                "body": {"role": role},
            }
        ],
        [(_DOORS["project_role"], pid)],
        card=("act", {}),
        done=f"已把 {shown} 改成{_ROLE[role]}。",
    )


async def _channel(db, access, body: ActIn, me: Person) -> _Plan:
    title = _need(body.title, "title")
    ref = refs.parse(_need(body.where, "where"))
    if ref is None or ref.kind != "project":
        raise BadRequestError(say("assistantRefUnknown", ref=body.where))
    project = await address.project_by_ref(db, ref.slug or "")
    if project is None:
        raise NotFoundError.for_resource("project", ref.slug or "")
    await access.project(project.id)
    about = body.text.strip()
    private = "私密" if body.members_only else ""
    return _Plan(
        gate.Action(
            "channel",
            f"在项目「{project.name}」新建{private}频道「{title}」"
            + (f"，说明：{about}" if about else ""),
            # Empty until someone writes in it, and archived as easily.
            low=True,
        ),
        [
            {
                "method": "POST",
                "path": "/topics",
                "body": {
                    "project_id": str(project.id),
                    "title": title,
                    **({"description": about} if about else {}),
                    "members_only": body.members_only,
                },
            }
        ],
        [(_DOORS["channel"], str(project.id))],
        card=("act", {}),
        done=f"已在项目「{project.name}」建好频道「{title}」。",
    )


def _fetch(body: ActIn) -> _Plan:
    url = _need(body.url, "url")
    asked = f"，了解：{body.prompt.strip()}" if body.prompt.strip() else ""
    return _Plan(
        # A documentation site Claude Code reads without asking is read with
        # no look here either.
        gate.Action("fetch", f"读取网页 {url}{asked}", low=preapproved(url)),
        [
            {
                "method": "POST",
                "path": "/fetch",
                "body": {
                    "url": url,
                    **({"prompt": body.prompt} if body.prompt else {}),
                },
            }
        ],
        [(_DOORS["fetch"], None)],
        card=None,
    )


@router.post("/act", include_in_schema=False)
async def agent_act(
    body: ActIn,
    resolver: ActorResolverDep,
    db: DbSession,
    questions: Questions,
) -> dict:
    """An action 芝士 is about to take for the person, through its gate: let
    through (``done``, with the pass and the calls that do it) or turned into
    a card the person confirms (``asked``, and why)."""
    started = time.monotonic()
    claims = resolver.delegation()
    if claims is None or claims.user_id is None or not claims.work:
        raise AuthenticationRequiredError(say("invalidToken"))
    me = await _person(resolver, db)
    access = ProjectPages(db, resolver)
    if body.kind == "message":
        plan = await _message(db, access, body, me)
    elif body.kind == "task":
        plan = await _task(db, access, body, me)
    elif body.kind == "review":
        plan = await _review(db, access, body, me)
    elif body.kind == "respond":
        plan = await _respond(db, body, me)
    elif body.kind == "member":
        plan = await _member(db, access, body, me)
    elif body.kind == "channel":
        plan = await _channel(db, access, body, me)
    else:
        plan = _fetch(body)
    conversation = uuid.UUID(claims.conversation) if claims.conversation else None
    verdict = await gate.judge(
        db, user_id=me.user_id, conversation_id=conversation, action=plan.action
    )
    await gate.keep(
        db,
        user_id=me.user_id,
        conversation_id=conversation,
        work=claims.work,
        action=plan.action,
        verdict=verdict,
        started=started,
    )
    await db.commit()
    if verdict.allowed:
        token = await passes.issue(claims.work, plan.doors)
        if token is not None:
            return ok(
                {"done": True, "pass": token, "calls": plan.calls, "said": plan.done}
            )
    if plan.card is None:
        return ok({"done": False, "reason": verdict.reason, "card": None})
    kind, payload = plan.card
    if kind == "act":
        payload = {
            "summary": plan.action.summary,
            "reason": verdict.reason,
            "calls": plan.calls,
            "said": plan.done,
        }
    card = await show_proposal(db, questions, claims, kind, payload)
    return ok({"done": False, "reason": verdict.reason, "card": card})


#: How long one wait on a card holds its request open; 芝士's tool asks again
#: until the person decides, the answer is stopped, or ``CARD_WAIT_S`` passes.
WAIT_S = 50.0
#: How long an answer waits on one card in all. A person who has left the
#: panel decides later; the answer ends, and the next question tells 芝士 how
#: the card ended (``personal.service.ask``).
CARD_WAIT_S = 600.0
#: How often a waiting request looks at the card again.
LOOK_EVERY_S = 1.0


class WaitIn(BaseModel):
    card: int


@router.post("/wait", include_in_schema=False)
async def agent_wait(body: WaitIn, resolver: ActorResolverDep, db: DbSession) -> dict:
    """How a card 芝士 put up ended: ``sent`` (the person confirmed it, and
    it was done as them) or ``dismissed``; still ``pending`` after
    ``WAIT_S``, when the tool asks again. The answer told is the answer that
    no longer needs telling (``proposals.told``)."""
    claims = resolver.delegation()
    if claims is None or claims.user_id is None or not claims.work:
        raise AuthenticationRequiredError(say("invalidToken"))
    until = time.monotonic() + WAIT_S
    while True:
        row = await proposals.mine(db, claims.user_id, body.card)
        if row.state != "pending":
            proposals.told(row)
            await db.commit()
            return ok({"state": row.state, "said": proposals.outcome(row)})
        waited = (datetime.now(UTC) - row.created_at).total_seconds()
        # Ends the transaction, so the next look sees the person's answer and
        # the connection goes back to the pool while it waits.
        await db.rollback()
        if waited >= CARD_WAIT_S:
            return ok({"state": "pending", "said": say("assistantCardStillWaiting")})
        if time.monotonic() >= until:
            return ok({"state": "pending"})
        await asyncio.sleep(LOOK_EVERY_S)


_LEVELS = {
    "all": "所有新消息",
    "mentions": "只在 @我和我参与的支线有回复时",
    "mute": "静音",
}


class NotifyIn(BaseModel):
    where: str
    level: str


@router.post("/notify", include_in_schema=False)
async def agent_notify(
    body: NotifyIn, resolver: ActorResolverDep, db: DbSession, questions: Questions
) -> dict:
    """cheese_settings_set on a channel: how much of it reaches the person,
    changed for them and kept as a change they can undo."""
    claims = resolver.delegation()
    if claims is None or claims.user_id is None or not claims.work:
        raise AuthenticationRequiredError(say("invalidToken"))
    me = await _person(resolver, db)
    if body.level not in _LEVELS:
        raise BadRequestError(
            say("assistantActNeeds", what="level: all / mentions / mute")
        )
    project, place, _ = await _place(db, ProjectPages(db, resolver), body.where)
    room = place.room
    topics = TopicService(db)
    before = (await topics.notify_levels(project.id, me.handle)).get(room.id)
    was = before[0].value if before else "mentions"
    await topics.set_notify_level(room.id, me.handle, body.level)
    row = await changes.report(
        db,
        user_id=me.user_id,
        work=claims.work,
        kind="notify",
        target=room.id,
        payload={
            "title": f"频道「{room.title}」的提醒",
            "project": str(project.id),
            "changed": {"level": {"label": "提醒", "before": was, "after": body.level}},
        },
    )
    await db.commit()
    await tell_change(questions, claims, changes.view(row))
    return ok({"text": f"已把频道「{room.title}」的提醒改成：{_LEVELS[body.level]}。"})


async def _notify_back(db, resolver, same, parts: list[str], redo: bool) -> dict:
    """Put a channel's reminders back as they were before the answer, or as
    it left them, as the person's own change."""
    actor = await resolver.resolve()
    first, last = same[0], same[-1]
    level = (last if redo else first).payload["changed"]["level"][
        "after" if redo else "before"
    ]
    await TopicService(db).set_notify_level(
        uuid.UUID(first.target), actor.handle, level
    )
    return {"topic": first.target, "level": level}


changes.register("notify", parts=lambda payload: ["level"], apply=_notify_back)
