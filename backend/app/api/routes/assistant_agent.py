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

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.assistant_deps import ProjectPages
from app.api.auth import ActorResolverDep
from app.api.awaiting_asks import MembershipAsk, RunAsk, TaskAsk
from app.api.awaiting_deps import all_waiting
from app.api.request_deps import DbSession
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import AuthenticationRequiredError, BadRequestError
from app.core.sentences import say
from app.domain.agent.personal import reading
from app.domain.assistant import refs
from app.domain.notification.preferences import local_time
from app.domain.project import address
from app.domain.project.services import ProjectService
from app.domain.room_task.awaiting import WaitingItem
from app.domain.user.services import timezones_by_ids

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
    role = f"，身份：{item.role}" if item.role else ""
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
    if ref is None or ref.kind not in reading.KINDS:
        raise BadRequestError(say("assistantRefUnknown", ref=body.ref))
    reader = reading.Reader(session=db, user_id=me.user_id, timezone=me.timezone)
    text = await reading.read(reader, ProjectPages(db, resolver), ref, body.page)
    return ok({"text": text})
