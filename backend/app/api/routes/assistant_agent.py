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
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.assistant_acts import (
    ASK,
    DOORS,
    MESSAGE,
    RANK,
    ROLE,
    ActIn,
    Person,
    Plan,
    announce,
    category,
    invite_code,
    named,
    need,
    problem_review,
    project_set,
    respond,
    space_member,
    space_set,
    team_member,
)
from app.api.assistant_deps import (
    ProjectPages,
    show_proposal,
    tell_change,
    tell_handed,
    tell_rule,
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
from app.domain.agent.personal import project_desk, reading, rosters, space_desk
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
from app.domain.user.services import (
    names_by_handles,
    timezones_by_ids,
)

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
_ASK_REF = {
    "project_invite": "invites",
    "project_transfer": "transfers",
    "team_invite": "team-invites",
    "join_request": "join-requests",
}


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
    role = f"，身份：{ROLE.get(item.role, item.role)}" if item.role else ""
    said = f"\n  附言：{item.message}" if item.message else ""
    return (
        f"- {item.person.name}{ASK[item.kind]}「{item.place.name}」{role}，"
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
            f"{item.claims} 份申请待通过" if item.claims else "",
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
    text = body.ref.strip().strip("/")
    if text == "teams":
        return ok({"text": await rosters.my_teams(db, me.user_id)})
    if text == "spaces":
        return ok({"text": await rosters.my_spaces(db, me.user_id)})
    people = _SPACE_PEOPLE.fullmatch(text)
    if people:
        return ok({"text": await rosters.space(db, me.user_id, int(people[1]))})
    desk = _SPACE_DESK.fullmatch(text)
    if desk:
        return ok({"text": await space_desk.desk(db, me.user_id, int(desk[1]))})
    if text.endswith("/settings") and (
        found := refs.parse(text.removesuffix("/settings"))
    ):
        if found.kind == "project":
            project = await address.project_by_ref(db, found.slug or "")
            if project is None:
                raise NotFoundError.for_resource("project", found.slug or "")
            channels = await ProjectPages(db, resolver).channels(project.id)
            said = await project_desk.settings(db, project, channels)
            return ok({"text": said})
    ref = refs.parse(body.ref)
    if ref is not None and ref.kind == "team" and (ref.id or "").isdigit():
        return ok({"text": await rosters.team(db, me.user_id, int(ref.id or 0))})
    if ref is not None and ref.kind in _WAITING_KINDS:
        return ok({"text": await _waiting_one(db, me, ref)})
    if ref is None or ref.kind not in reading.KINDS:
        raise BadRequestError(say("assistantRefUnknown", ref=body.ref))
    reader = reading.Reader(session=db, user_id=me.user_id, timezone=me.timezone)
    text = await reading.read(reader, ProjectPages(db, resolver), ref, body.page)
    return ok({"text": text})


#: Who is in a space: its managers and members (`rosters.space`).
_SPACE_PEOPLE = re.compile(r"spaces/(\d{1,9})/people")
#: What a space's manager sees of it (`space_desk.desk`).
_SPACE_DESK = re.compile(r"spaces/(\d{1,9})/manage")

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
                return line + "\n答复用 cheese_inbox(action=respond)。"
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


async def _message(db, access, body: ActIn, me: Person) -> Plan:
    text = need(body.text, "text")
    project, place, ref = await _place(db, access, need(body.where, "where"))
    name = where_of(place)
    everyone = (
        place.task is None and place.thread is None and str(place.room.kind) == "root"
    )
    return Plan(
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
        [(MESSAGE, str(place.conversation_id))],
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


async def _task(db, access, body: ActIn, me: Person) -> Plan:
    title, text = need(body.title, "title"), need(body.text, "text")
    project, place, _ = await _place(db, access, need(body.where, "where"))
    room = place.room
    return Plan(
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
        [(DOORS["task"], str(room.id)), (MESSAGE, None)],
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


async def _review(db, access, body: ActIn, me: Person) -> Plan:
    ref = refs.parse(need(body.ref, "ref"))
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
        note = need(body.text, "text")
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
    door = DOORS["accept" if body.choice == "accept" else "reject"]
    return Plan(
        gate.Action(
            "review",
            f"{verb.split('，')[0]}任务「{place.task.title}」（项目"
            f"「{project.name}」，改动：{card.change_subject or '未写'}"
            f"{reading.checks_of(card)}）"
            + (f"，{verb.split('，', 1)[1]}" if "，" in verb else ""),
        ),
        [call],
        [(door, None)],
        card=("act", {}),
        done=f"已{verb.split('，')[0]}任务「{place.task.title}」。",
    )


async def _member(db, access, body: ActIn, me: Person) -> Plan:
    who, shown = await named(db, need(body.who, "who").lstrip("@"))
    ref = refs.parse(need(body.where, "where"))
    if ref is None:
        raise BadRequestError(say("assistantRefUnknown", ref=body.where))
    if ref.kind in ("team", "space") and (ref.id or "").isdigit():
        of = team_member if ref.kind == "team" else space_member
        return await of(db, int(ref.id or 0), body, who, shown)
    if ref.kind == "channel":
        project, place, _ = await _place(db, access, body.where)
        room = place.room
        if body.remove:
            return Plan(
                gate.Action(
                    "member",
                    f"将 {shown} 移出频道「{room.title}」（项目「{project.name}」）",
                    ("remove_member",),
                ),
                [{"method": "DELETE", "path": f"/topics/{room.id}/members/{who}"}],
                [(DOORS["channel_remove"], str(room.id))],
                card=("act", {}),
                done=f"已把 {shown} 移出频道「{room.title}」。",
            )
        return Plan(
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
            [(DOORS["channel_add"], str(room.id))],
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
        return Plan(
            gate.Action(
                "member",
                f"将{ROLE.get(seat or '', '不在项目中的')} {shown} 移出项目「{project.name}」",
                ("remove_member",),
            ),
            [{"method": "DELETE", "path": f"/projects/{pid}/members/{who}"}],
            [(DOORS["project_remove"], pid)],
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
        return Plan(
            gate.Action(
                "member",
                f"邀请 {shown} 以{ROLE[role]}身份加入项目「{project.name}」",
            ),
            [
                {
                    "method": "POST",
                    "path": f"/projects/{pid}/invitations",
                    "body": {"user_handle": who, "role": role},
                }
            ],
            [(DOORS["project_invite"], pid)],
            card=("act", {}),
            done=f"已邀请 {shown} 加入项目「{project.name}」，等对方接受。",
        )
    lowers = RANK.index(role) > RANK.index(seat)
    return Plan(
        gate.Action(
            "member",
            f"将 {shown} 在项目「{project.name}」的角色从{ROLE[seat]}改为{ROLE[role]}",
            ("lower_role",) if lowers else (),
        ),
        [
            {
                "method": "PUT",
                "path": f"/projects/{pid}/members/{who}/role",
                "body": {"role": role},
            }
        ],
        [(DOORS["project_role"], pid)],
        card=("act", {}),
        done=f"已把 {shown} 改成{ROLE[role]}。",
    )


async def _channel(db, access, body: ActIn, me: Person) -> Plan:
    title = need(body.title, "title")
    ref = refs.parse(need(body.where, "where"))
    if ref is None or ref.kind != "project":
        raise BadRequestError(say("assistantRefUnknown", ref=body.where))
    project = await address.project_by_ref(db, ref.slug or "")
    if project is None:
        raise NotFoundError.for_resource("project", ref.slug or "")
    await access.project(project.id)
    about = body.text.strip()
    private = "私密" if body.members_only else ""
    return Plan(
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
        [(DOORS["channel"], str(project.id))],
        card=("act", {}),
        done=f"已在项目「{project.name}」建好频道「{title}」。",
    )


def _fetch(body: ActIn) -> Plan:
    url = need(body.url, "url")
    asked = f"，了解：{body.prompt.strip()}" if body.prompt.strip() else ""
    return Plan(
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
        [(DOORS["fetch"], None)],
        card=None,
    )


#: What 芝士 does on a space the person manages (`assistant_acts`).
_SPACE_ACTS = {
    "space_set": space_set,
    "announce": announce,
    "category": category,
    "invite_code": invite_code,
    "problem_review": problem_review,
}


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
        plan = await respond(db, body, me)
    elif body.kind == "member":
        plan = await _member(db, access, body, me)
    elif body.kind == "channel":
        plan = await _channel(db, access, body, me)
    elif body.kind == "project_set":
        plan = await project_set(db, access, body)
    elif body.kind in _SPACE_ACTS:
        plan = await _SPACE_ACTS[body.kind](db, me, body)
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
        token = await passes.issue(claims, plan.doors)
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
    """cheese_settings on a channel: how much of it reaches the person,
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
    name = need(body.name, "name")
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
                name, body.description, body.type, need(body.text, "text")
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
    #: Fire on the first thing only, and run in this conversation.
    once: bool = False
    #: ``mention``: only these people, by handle or name.
    people: list[str] | None = None
    #: ``mention``: only in this channel or task, by its ref.
    where: str = ""
    #: ``filed`` / ``decided``: this task, by its ref.
    task: str = ""
    #: ``filed`` / ``decided``: any task the person asked for.
    mine: bool = False
    #: ``decided``: only ``accepted`` or only ``rejected``.
    outcome: str = ""


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
    if routine_personal.once(row) and row.trigger != "schedule":
        nxt += "，等到一次就结束"
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
    spec: dict = {
        "once": body.once,
        "mine": body.mine,
        "outcome": body.outcome,
    }
    if body.project:
        project = await address.project_by_ref(db, body.project)
        if project is None:
            raise NotFoundError.for_resource("project", body.project)
        await access.project(project.id)
        spec |= {"project": str(project.id), "projectName": project.name}
    if body.people:
        handles = [(await named(db, who.lstrip("@")))[0] for who in body.people]
        names = await names_by_handles(db, handles)
        spec |= {"from": handles, "fromNames": [names.get(h, h) for h in handles]}
    if body.where:
        _, place, _ = await _place(db, access, body.where)
        spec |= (
            {"place": {"kind": "task", "id": str(place.task.id)}}
            if place.task is not None
            else {"place": {"kind": "topic", "id": str(place.room.id)}}
        ) | {"placeName": where_of(place)}
    if body.task:
        _, place, _ = await _place(db, access, body.task)
        if place.task is None:
            raise BadRequestError(say("assistantRefUnknown", ref=body.task))
        spec |= {"task": str(place.task.id), "taskTitle": place.task.title}
    return trigger, spec


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
            return ok({"text": "用户还没有自动化。"})
        return ok(
            {
                "text": f"用户的自动化（{len(rows)} 条，只做一次的和反复做的各最多 "
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
            title=need(body.title, "title"),
            note=body.note,
            trigger=trigger,
            spec=spec,
        )
        await db.commit()
        await tell_rule(questions, claims, routine_personal.card(row))
        return ok({"text": "已建好自动化：\n" + _routine_line(row, me.timezone)})
    try:
        routine_id = uuid.UUID(need(body.routine, "routine"))
    except ValueError as exc:
        raise NotFoundError.for_resource("routine", body.routine) from exc
    row = await routine_personal.one(db, me.user_id, routine_id)
    if row is None:
        raise NotFoundError.for_resource("routine", body.routine)
    if body.action == "delete":
        title = row.title
        await db.delete(row)
        await db.commit()
        return ok({"text": f"已删除自动化「{title}」。"})
    if body.action == "pause":
        routine_personal.pause(row)
    elif body.action == "resume":
        routine_personal.resume(row)
    else:
        trigger, spec = (
            await _routine_spec(db, ProjectPages(db, resolver), body)
            if body.trigger or body.freq or body.project or body.task
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
    await tell_rule(questions, claims, routine_personal.card(row))
    return ok({"text": "自动化现在是：\n" + _routine_line(row, me.timezone)})


class NotifyMeIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class TodoItemIn(BaseModel):
    content: str = Field(min_length=1, max_length=200)
    status: Literal["pending", "in_progress", "completed"]


class TodoIn(BaseModel):
    todos: list[TodoItemIn] = Field(max_length=30)


@router.post("/todo", include_in_schema=False)
async def agent_todo(body: TodoIn, resolver: ActorResolverDep) -> dict:
    """cheese_todo: the steps of a long piece of work and how far it got. The
    panel shows them from the call itself, as it is written, and keeps them
    with the answer; here they are only checked and counted back."""
    claims = resolver.delegation()
    if claims is None or claims.user_id is None or not claims.work:
        raise AuthenticationRequiredError(say("invalidToken"))
    done = sum(1 for item in body.todos if item.status == "completed")
    going = [item for item in body.todos if item.status == "in_progress"]
    told = f"清单已更新：{done}/{len(body.todos)} 项已完成。"
    if len(going) > 1:
        told += "同时只该有一项进行中。"
    return ok({"text": told})


@router.post("/notify-me", include_in_schema=False)
async def agent_notify_me(
    body: NotifyMeIn, resolver: ActorResolverDep, db: DbSession
) -> dict:
    """cheese_notify: a notification to the person, from their 芝士, that
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
# `cheese_library(action=give)`、`cheese_library(action=take)`）。


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
    """cheese_library(action=give) 的第一步：一次放 ``size`` 个字节进用户资料库的
    上传，和它怎么切成段。放不下（总量、单个文件的上限）就在这里说。"""
    _, shelf = await _my_library(resolver, db)
    name = library_service.clean_upload_name(body.name)
    await library_records.refuse_past_room(db, shelf, body.size)
    return ok(await library_records.upload_slot(shelf, name, body.size))


class FilePartIn(BaseModel):
    upload: str
    part: int = Field(ge=1)
    md5: str


@router.post("/files/part", include_in_schema=False)
async def agent_file_part(
    body: FilePartIn, resolver: ActorResolverDep, db: DbSession
) -> dict:
    """cheese_library(action=give) 的第二步：一段的地址，电脑把这一段原样 PUT 过去，带上给的
    请求头；字节的 MD5 对不上的存储不收。"""
    _, shelf = await _my_library(resolver, db)
    part = await library_records.upload_part(shelf, body.upload, body.part, body.md5)
    return ok({**part, "url": _reachable(part["url"])})


class FileHandedIn(BaseModel):
    upload: str
    #: 整份的 SHA-256，和每一段（按顺序）的 MD5，十六进制。
    sha256: str
    parts: list[str]
    name: str = Field(min_length=1, max_length=512)


@router.post("/files/handed", include_in_schema=False)
async def agent_file_handed(
    body: FileHandedIn, resolver: ActorResolverDep, db: DbSession, questions: Questions
) -> dict:
    """cheese_library(action=give) 的最后一步：传上来的那一份登记进用户的资料库，回答里出现
    一张文件卡片。撞名不覆盖，拿下一个 ``(n)``。"""
    me, shelf = await _my_library(resolver, db)
    upload = library_records.received(shelf, body.upload, body.sha256, body.parts)
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
    """cheese_library(action=take)：用户资料库里一份文件的地址，几分钟内有效，电脑拿它
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
                "读不成文字；要处理就用 cheese_library(action=take) 放进电脑。"
            }
        )
    return ok({"text": str(found["content"])})


def _size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.1f} MB"
