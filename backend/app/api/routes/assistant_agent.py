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
from pydantic import BaseModel, Field

from app.api.assistant_deps import (
    ProjectPages,
    show_proposal,
    tell_change,
    tell_handed,
    where_of,
)
from app.api.auth import ActorResolverDep
from app.api.awaiting_asks import MembershipAsk, RunAsk, TaskAsk
from app.api.awaiting_deps import all_waiting
from app.api.deps import get_consumptions
from app.api.request_deps import DbSession
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.auth.project_roles import standing
from app.core.config import settings
from app.core.errors import AuthenticationRequiredError, BadRequestError, NotFoundError
from app.core.sentences import say
from app.domain.agent.personal import reading
from app.domain.agent.session_host.consumptions import Consumptions
from app.domain.assistant import changes, gate, passes, proposals, refs
from app.domain.assistant import memory as assistant_memory
from app.domain.fetch.preapproved import preapproved
from app.domain.library import listing as library_listing
from app.domain.library import records as library_records
from app.domain.library import service as library_service
from app.domain.library.shelf import Shelf
from app.domain.memory.files import MemoryFileError, parse_memory_file
from app.domain.notification.preferences import local_time
from app.domain.project import address
from app.domain.project.services import ProjectService
from app.domain.review.services import deliverable_revision
from app.domain.room_task.awaiting import WaitingItem
from app.domain.routine import personal as routine_personal
from app.domain.team.services import team_service
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
    #: Put it to the person if the gate stops it, rather than hand it back:
    #: 芝士 has nothing to put right, only the person's yes is missing.
    confirm: bool = False


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
    through (``done``, with the pass and the calls that do it), handed back to
    芝士 with the reason (``returned``), or turned into a card the person
    confirms (``card``). A look's stop goes back to 芝士 the first few times in
    an answer (``gate.RETURNS_PER_ANSWER``) unless it asks for a card
    (``confirm``) or nobody is there to ask."""
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
    returned = (
        not verdict.allowed
        and verdict.stage in ("quick", "reasoned")
        and not body.confirm
        and not await _unattended(questions, claims.work)
        and await gate.returned_in(db, me.user_id, claims.work)
        < gate.RETURNS_PER_ANSWER
    )
    await gate.keep(
        db,
        user_id=me.user_id,
        conversation_id=conversation,
        work=claims.work,
        action=plan.action,
        verdict=verdict,
        started=started,
        returned=returned,
    )
    await db.commit()
    if returned:
        return ok(
            {
                "done": False,
                "returned": True,
                "reason": verdict.reason,
                "said": say("assistantActReturned", reason=verdict.reason),
            }
        )
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


async def _unattended(questions: Consumptions, work: str) -> bool:
    """Is this answer a routine's run, with nobody there to confirm?"""
    try:
        asked = await questions.get(work)
    except RuntimeError:  # no Valkey: nothing runs on its own
        return False
    return asked is not None and bool(asked.data.get("run"))


@router.post("/wait", include_in_schema=False)
async def agent_wait(
    body: WaitIn, resolver: ActorResolverDep, db: DbSession, questions: Questions
) -> dict:
    """How a card 芝士 put up ended: ``sent`` (the person confirmed it, and
    it was done as them) or ``dismissed``; still ``pending`` after
    ``WAIT_S``, when the tool asks again. The answer told is the answer that
    no longer needs telling (``proposals.told``)."""
    claims = resolver.delegation()
    if claims is None or claims.user_id is None or not claims.work:
        raise AuthenticationRequiredError(say("invalidToken"))
    if await _unattended(questions, claims.work):
        # A routine running on its own: nobody is there to decide now. The
        # card stays for the person (``routine.personal.KEPT_FOR``).
        return ok({"state": "pending", "said": say("assistantCardLeftForLater")})
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


class MemoryIn(BaseModel):
    action: Literal["save", "read", "forget"]
    name: str = ""
    description: str = ""
    type: str = "user"
    text: str = ""


@router.post("/memory", include_in_schema=False)
async def agent_memory(
    body: MemoryIn, resolver: ActorResolverDep, db: DbSession, questions: Questions
) -> dict:
    """cheese_memory: what the person's 芝士 remembers about them
    (``assistant.memory``), saved, read or forgotten in the person's panel.
    Each save and forget is kept as a change the person can undo there. A
    routine's run does not write it, and nothing is kept while the person has
    turned memory off."""
    claims = resolver.delegation()
    if claims is None or claims.user_id is None or not claims.work:
        raise AuthenticationRequiredError(say("invalidToken"))
    me = await _person(resolver, db)
    name = _need(body.name, "name")
    if body.action == "read":
        found = await assistant_memory.read(db, me.handle, name)
        if found is None:
            raise NotFoundError.for_resource("memory", name)
        return ok({"text": f"{found.description}\n\n{found.body}"})
    if not (await gate.rules_of(db, me.user_id)).memory:
        raise BadRequestError(say("assistantMemoryOff"))
    if await _unattended(questions, claims.work):
        raise BadRequestError(say("assistantMemoryNotInRuns"))
    path = assistant_memory.path_of(name)
    if body.action == "save":
        try:
            memory = assistant_memory.made(
                name, body.description, body.type, _need(body.text, "text")
            )
        except MemoryFileError as exc:
            raise BadRequestError(str(exc)) from exc
        before = await assistant_memory.save(db, me.handle, memory, by=me.handle)
        after: str | None = memory.text()
        label, said = memory.description, "已记住：" + memory.description
    else:
        before = await assistant_memory.put(db, me.handle, path, None, by=me.handle)
        if before is None:
            raise NotFoundError.for_resource("memory", name)
        after = None
        label = _described(before) or name
        said = "已忘记：" + label
    row = await changes.report(
        db,
        user_id=me.user_id,
        work=claims.work,
        kind="memory",
        target=path,
        payload={
            "title": "记忆",
            "changed": {"memory": {"label": label, "before": before, "after": after}},
        },
    )
    await db.commit()
    await tell_change(questions, claims, changes.view(row))
    return ok({"text": said})


def _described(content: str) -> str:
    try:
        return parse_memory_file(content).description
    except MemoryFileError:
        return ""


async def _memory_back(db, resolver, same, parts: list[str], redo: bool) -> dict:
    """Put a memory back as it was before the answer, or as it left it, as
    the person's own change."""
    actor = await resolver.resolve()
    first, last = same[0], same[-1]
    content = (last if redo else first).payload["changed"]["memory"][
        "after" if redo else "before"
    ]
    await assistant_memory.put(db, actor.handle, first.target, content, by=actor.handle)
    return {"memory": first.target, "kept": content is not None}


changes.register("memory", parts=lambda payload: ["memory"], apply=_memory_back)


# --- what 芝士 does on its own ----------------------------------------------

#: How many notifications 芝士 may send the person in an hour.
NOTIFY_ME_PER_HOUR = 10


class RoutineIn(BaseModel):
    action: Literal["create", "list", "change", "pause", "resume", "delete"]
    #: The routine's id, for all but ``create`` and ``list``.
    routine: str = ""
    title: str = Field(default="", max_length=120)
    #: What each run does, in 芝士's words.
    note: str = Field(default="", max_length=4000)
    trigger: str = ""
    #: A schedule: ``once`` (``at``), ``hourly`` (``minute``), ``daily``,
    #: ``weekly`` (``weekdays``, 0 for Monday) or ``monthly`` (``day``), at
    #: ``time`` (HH:MM) on the person's clock.
    freq: str = ""
    time: str = ""
    minute: int | None = None
    weekdays: list[int] | None = None
    day: int | None = None
    at: str = ""
    #: An event routine's project, by its short name: only what happens there.
    project: str = ""


def _routine_line(row, tz: str | None) -> str:
    state = {"credits": "（额度用完，已暂停）", "away": "（长时间未登录，已暂停）"}.get(
        row.paused,
        {"paused": "（已暂停）", "done": "（已运行过）"}.get(row.state, ""),
    )
    nxt = (
        f"，下次运行 {_when(row.next_run_at, tz)}"
        if row.next_run_at and row.state == "active"
        else ""
    )
    return (
        f"- 「{row.title}」{state}：{routine_personal.describe(row)}{nxt}；"
        f"要做的事：{row.note or row.title}（编号 {row.id}）"
    )


async def _routine_spec(db, access, body: RoutineIn) -> tuple[str, dict]:
    trigger = body.trigger or "schedule"
    if trigger == "schedule":
        spec = {
            k: v
            for k, v in {
                "freq": body.freq,
                "time": body.time,
                "minute": body.minute,
                "weekdays": body.weekdays,
                "day": body.day,
                "at": body.at,
            }.items()
            if v not in ("", None)
        }
        return trigger, spec
    if not body.project:
        return trigger, {}
    project = await address.project_by_ref(db, body.project)
    if project is None:
        raise NotFoundError.for_resource("project", body.project)
    await access.project(project.id)
    return trigger, {"project": str(project.id), "projectName": project.name}


@router.post("/routine", include_in_schema=False)
async def agent_routine(
    body: RoutineIn, resolver: ActorResolverDep, db: DbSession, questions: Questions
) -> dict:
    """cheese_routine: the person's routines, made and changed by 芝士 when
    they ask for one. Only the person is affected, so it is done at once; a
    run cannot make or change one, since nobody asked it to."""
    claims = resolver.delegation()
    if claims is None or claims.user_id is None or not claims.work:
        raise AuthenticationRequiredError(say("invalidToken"))
    me = await _person(resolver, db)
    if body.action == "list":
        rows = await routine_personal.mine(db, me.user_id)
        if not rows:
            return ok({"text": "用户还没有例行事务。"})
        return ok(
            {
                "text": f"用户的例行事务（{len(rows)} 条，最多 "
                f"{routine_personal.MAX_ROUTINES} 条）：\n"
                + "\n".join(_routine_line(r, me.timezone) for r in rows)
            }
        )
    if await _unattended(questions, claims.work) or not claims.conversation:
        raise BadRequestError(say("assistantRoutineInRun"))
    conversation = uuid.UUID(claims.conversation)
    if body.action == "create":
        trigger, spec = await _routine_spec(db, ProjectPages(db, resolver), body)
        row = await routine_personal.create(
            db,
            user_id=me.user_id,
            conversation_id=conversation,
            title=_need(body.title, "title"),
            note=body.note,
            trigger=trigger,
            spec=spec,
        )
        await db.commit()
        return ok({"text": "已建好例行事务：\n" + _routine_line(row, me.timezone)})
    try:
        routine_id = uuid.UUID(_need(body.routine, "routine"))
    except ValueError as exc:
        raise NotFoundError.for_resource("routine", body.routine) from exc
    row = await routine_personal.one(db, me.user_id, routine_id)
    if row is None:
        raise NotFoundError.for_resource("routine", body.routine)
    if body.action == "delete":
        title = row.title
        await db.delete(row)
        await db.commit()
        return ok({"text": f"已删除例行事务「{title}」。"})
    if body.action == "pause":
        routine_personal.pause(row)
    elif body.action == "resume":
        routine_personal.resume(row)
    else:
        trigger, spec = (
            await _routine_spec(db, ProjectPages(db, resolver), body)
            if body.trigger or body.freq or body.project
            else (None, None)
        )
        await routine_personal.change(
            db,
            row,
            conversation_id=conversation,
            title=body.title or None,
            note=body.note or None,
            trigger=trigger,
            spec=spec,
        )
    await db.commit()
    return ok({"text": "例行事务现在是：\n" + _routine_line(row, me.timezone)})


class NotifyMeIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


@router.post("/notify-me", include_in_schema=False)
async def agent_notify_me(
    body: NotifyMeIn, resolver: ActorResolverDep, db: DbSession
) -> dict:
    """cheese_notify_me: a notification to the person, from their 芝士, that
    opens this conversation."""
    claims = resolver.delegation()
    if claims is None or claims.user_id is None or not claims.work:
        raise AuthenticationRequiredError(say("invalidToken"))
    sent = await routine_personal.notified_lately(db, claims.user_id)
    if sent >= NOTIFY_ME_PER_HOUR:
        raise BadRequestError(say("assistantNotifyMeLimit", max=NOTIFY_ME_PER_HOUR))
    await routine_personal.notify_me(
        db,
        user_id=claims.user_id,
        conversation_id=uuid.UUID(claims.conversation) if claims.conversation else None,
        work=claims.work,
        text=body.text,
    )
    await db.commit()
    return ok({"text": "已通知用户。"})


# --- 用户的资料库：芝士交给用户的、用户附给芝士的文件 -------------------------------
#
# 一个人的资料库是他名下那个团队的（`library.shelf.Shelf`）。文件在芝士的电脑和存储
# 之间直接走：这里给一个只管一份文件、几分钟就失效的地址，电脑自己去传、去取，
# 字节不经过芝士那一侧，电脑里也没有任何凭据（`sandbox/cheese` 的
# `cheese_file_give`、`cheese_file_to_computer`）。


async def _my_library(resolver, db) -> tuple[Person, Shelf]:
    claims = resolver.delegation()
    if claims is None or claims.user_id is None or not claims.work:
        raise AuthenticationRequiredError(say("invalidToken"))
    me = await _person(resolver, db)
    team = await team_service(db).ensure_personal_team(me.user_id)
    return me, Shelf.of_team(team.id)


def _reachable(url: str) -> str:
    """一个电脑上能直接用的地址：后端自己的地址（以 ``/`` 开头）补上平台对外的
    那一段；对象存储给的本来就是完整的。"""
    if url.startswith("/"):
        return f"{settings.frontend_url.rstrip('/')}/api{url}"
    return url


class FileSlotIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=1)


@router.post("/files/slot", include_in_schema=False)
async def agent_file_slot(
    body: FileSlotIn, resolver: ActorResolverDep, db: DbSession
) -> dict:
    """cheese_file_give 的第一步：一个放 ``size`` 个字节进用户资料库的地址。放不下
    （总量、单个文件的上限）就在这里说。"""
    _, shelf = await _my_library(resolver, db)
    name = library_service.clean_upload_name(body.name)
    await library_records.refuse_past_room(db, shelf, body.size)
    slot = await library_records.upload_slot(shelf, name, body.size)
    return ok({**slot, "url": _reachable(slot["url"])})


class FileHandedIn(BaseModel):
    upload: str
    name: str = Field(min_length=1, max_length=512)


@router.post("/files/handed", include_in_schema=False)
async def agent_file_handed(
    body: FileHandedIn, resolver: ActorResolverDep, db: DbSession, questions: Questions
) -> dict:
    """cheese_file_give 的最后一步：传上来的那一份登记进用户的资料库，回答里出现
    一张文件卡片。撞名不覆盖，拿下一个 ``(n)``。"""
    me, shelf = await _my_library(resolver, db)
    upload = await library_records.received(shelf, body.upload)
    name = await library_records.add(
        db,
        shelf,
        filename=library_service.clean_library_path(body.name),
        data=upload,
        added_by=me.handle,
        room_id=None,
    )
    await db.commit()
    claims = resolver.delegation()
    assert claims is not None
    await tell_handed(
        questions, claims, {"team": shelf.team_id, "path": name, "bytes": upload.size}
    )
    return ok({"path": name, "bytes": upload.size})


class FileLinkIn(BaseModel):
    path: str = Field(min_length=1, max_length=512)


@router.post("/files/link", include_in_schema=False)
async def agent_file_link(
    body: FileLinkIn, resolver: ActorResolverDep, db: DbSession
) -> dict:
    """cheese_file_to_computer：用户资料库里一份文件的地址，几分钟内有效，电脑拿它
    把文件取下来。"""
    _, shelf = await _my_library(resolver, db)
    name = body.path.strip().removeprefix("library/")
    stored = await library_records.stored_file(db, shelf, name)
    url = await library_records.download_url(db, shelf, name)
    return ok({"url": _reachable(url), "path": name, "bytes": stored.size})


class LibraryReadIn(BaseModel):
    #: ``library`` 列出全部，``library/<名字>`` 读那一份。
    ref: str
    page: int = Field(default=1, ge=1)


#: 列出资料库时最多列多少份；多了就说还有多少。
LIBRARY_LIST_LIMIT = 100


@router.post("/library", include_in_schema=False)
async def agent_library(
    body: LibraryReadIn, resolver: ActorResolverDep, db: DbSession
) -> dict:
    """cheese_read 读 ``library``：用户资料库里有哪些文件；读 ``library/<名字>``：
    那一份的文字（PDF 取文字层，别的二进制只说大小）。"""
    me, shelf = await _my_library(resolver, db)
    name = body.ref.strip().strip("/").removeprefix("library").strip("/")
    if not name:
        rows, more = await library_listing.page(
            db, shelf, {}, flat=True, limit=LIBRARY_LIST_LIMIT
        )
        if not rows:
            return ok({"text": "用户的资料库里还没有文件。"})
        lines = [
            f"- library/{row['path']}（{_size(row['bytes'])}，"
            f"{_when(datetime.fromisoformat(row['added_at']), me.timezone)}）"
            for row in rows
        ]
        if more:
            lines.append(f"（只列了最近的 {LIBRARY_LIST_LIMIT} 份）")
        return ok({"text": "\n".join(lines)})
    found = await library_records.read_text(db, shelf, name)
    if found.get("content") is None:
        return ok(
            {
                "text": f"library/{name} 是二进制文件（{_size(found['bytes'])}），"
                "读不成文字；要处理就用 cheese_file_to_computer 放进电脑。"
            }
        )
    return ok({"text": str(found["content"])})


def _size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.1f} MB"
