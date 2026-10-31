"""Routines: standing work on a clock or on a project event.

An AI teammate creates and edits one the same way a person does, and both take
effect at once: saving a rule is what makes work happen unattended, and nothing
waits on a second person to agree. What a person keeps to themselves is deleting
one, because that takes the rule away from everyone in the room.

**Who a rule belongs to.** Seeing one is the room's business: the people on that
room's roster, plus whoever manages the project. Being a member of the project
itself is NOT enough — a rule names what one room does on a clock, and the other
rooms of the project have no business reading who set what there. Editing one
(pausing, resuming, running by hand, and a change of its fields) is the same
audience: whoever may read it may change it. Deleting is narrower: the person it
notifies (``owner_handle``) or a project manager. Every response carries
``can_delete`` so the frontend knows whether to draw that button.

**Templates are not rules.** A saved template is a filled-in form kept to fill
the next one from, and it belongs to the project: it is the project's business,
not one room's, so the people who may read the project read the same list, and
may add to it or take from it. Nothing runs off a template, so nothing here
carries ``can_delete`` and no refusal narrows who may remove one.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import Page, ok, page, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core.config import settings
from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import notice_keys, say
from app.domain.agent_instance.adapters import project_team
from app.domain.identity.actor import Actor
from app.domain.membership.services import MemberService
from app.domain.project_permissions import Permission
from app.domain.room_task.place import place_or_404
from app.domain.routine import service as routines
from app.domain.routine.models import Routine, RoutineRun, RoutineTemplate
from app.domain.routine.service import RoutineService
from app.domain.routine.templates import RoutineTemplateService
from app.domain.routine.trigger import describe_trigger
from app.domain.topic.models import Topic
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.windows import month_end, month_of, month_start

router = APIRouter(prefix="", tags=["routines"])


class RoutineIn(BaseModel):
    #: The goal is the one thing a rule must say; its name may be left out and
    #: is then taken from the goal's first sentence.
    title: str = Field(default="", max_length=200)
    instructions: str = Field(min_length=1)
    #: 「补充说明」: anything else the teammate should know. May be left empty —
    #: nothing is invented to fill it.
    note: str = ""
    trigger: str = "schedule"
    spec: dict = Field(default_factory=dict)
    #: Unsaid: the owner's own zone, as their browser last reported it.
    timezone: str | None = None
    owner_handle: str | None = None
    agent_handle: str | None = None


class RoutinePatch(BaseModel):
    title: str | None = None
    instructions: str | None = None
    note: str | None = None
    trigger: str | None = None
    spec: dict | None = None
    timezone: str | None = None
    agent_handle: str | None = None


class RoutineTemplateIn(BaseModel):
    #: 名字和一条规则一样可以省：省了就从目标那一句里取（`name_of`）。
    title: str = Field(default="", max_length=200)
    #: 「目标」 — the same words the rule form's first field took. Called ``goal``
    #: here because that is the name a template goes by on the screen.
    goal: str = ""
    note: str = ""
    trigger: str = "schedule"
    spec: dict = Field(default_factory=dict)


class RoutineTemplateOut(BaseModel):
    """``_template`` 给出去的那一份，照抄过来当声明。

    只声明不校验（``typed_response`` 的 ``response_model=None``）：真正的形状还是
    那个函数拼的，这里写的用处是让 OpenAPI 认得出这个接口返回什么。
    """

    id: str
    title: str
    goal: str
    note: str
    trigger: str
    spec: dict


class RoutineTemplateListOut(Page[RoutineTemplateOut]):
    """这个项目里存的表，最近存的在最前面。"""


class RoutineTemplateDeleted(BaseModel):
    """一张模板被删掉，回的是它的 id。"""

    deleted: str


class ReportIn(BaseModel):
    status: str
    summary: str = ""
    outputs: list[str] = Field(default_factory=list)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _routine(
    row: Routine,
    *,
    can_delete: bool = False,
    room_archived: bool = False,
    last_run_status: str | None = None,
    credits_this_month: float = 0.0,
) -> dict:
    trigger_text = describe_trigger(row)
    return {
        **notice_keys(trigger_text=trigger_text),
        "id": str(row.id),
        "can_delete": can_delete,
        "room_archived": room_archived,
        "last_run_status": last_run_status,
        "credits_this_month": credits_this_month,
        "project_id": str(row.project_id),
        "topic_id": str(row.topic_id),
        "title": row.title,
        "instructions": row.instructions,
        "note": row.note,
        "trigger": row.trigger,
        "trigger_text": trigger_text,
        "spec": row.spec,
        "timezone": row.timezone,
        "state": row.state,
        "agent_handle": row.agent_handle,
        "owner_handle": row.owner_handle,
        "created_by": row.created_by,
        "next_run_at": _iso(row.next_run_at),
        "revision": row.revision,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _template(row: RoutineTemplate) -> dict:
    """一条模板给出去的形状，就是填表那几格：名字、目标、补充说明、触发方式、时间。

    没有 `trigger_text`：那一行由前端按读者此刻的语言现算（`routineWhenText`），
    后端算一句话过来，英文界面上会是一行中文。也没有「谁存的」「什么时候存的」——
    界面上没有这一格，不给出去就不会有人照着它画。
    """
    return {
        "id": str(row.id),
        "title": row.title,
        "goal": row.goal,
        "note": row.note,
        "trigger": row.trigger,
        "spec": row.spec,
    }


def _run(row: RoutineRun, *, credits: float | None = None) -> dict:
    return {
        "id": str(row.id),
        "routine_id": str(row.routine_id),
        "occurrence_key": row.occurrence_key,
        "trigger_detail": row.trigger_detail,
        "routine_revision": row.routine_revision,
        "scheduled_for": _iso(row.scheduled_for),
        "status": row.status,
        "summary": row.summary,
        "outputs": row.outputs,
        "error": row.error,
        "created_at": _iso(row.created_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "turn_id": str(row.turn_id) if row.turn_id else None,
        # 点数，本月窗口内。`None` 是「还没有数」—— 还没跑完，或者刚跑完花费还没落账
        # （几秒到几十秒）。写 0 是在说「这一轮没花钱」，两件事画出来不一样。
        "credits": credits,
    }


def _is_person(actor: Actor) -> bool:
    return actor.via == "token"


async def _manages_project(
    db: AsyncSession, actor: Actor, project_id: uuid.UUID
) -> bool:
    """May ``actor`` manage every channel of the project, and so every routine
    in them, not only their own?

    Credentials come first: a handle that arrived through the deprecated
    body/param fallback is whatever the caller typed, and anyone can type the
    owner's handle.
    """
    if not actor.authenticated:
        return False
    return await MemberService(db).manages(
        project_id, actor.handle, Permission.manage_channels
    )


async def _sees_room(
    db: AsyncSession,
    actor: Actor,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    *,
    room: Topic | None = None,
) -> bool:
    """May ``actor`` read what happens in this room: it is in the channel (the
    channel's own AI teammate included), or it manages the project.

    Narrower than ``authorize_topic_access``, and deliberately: that policy lets
    anyone in the project into a non-private room, which is right for the room's
    conversation and wrong for the rules of ONE of the project's rooms.
    """
    if not settings.authz_enforce_topic_access:
        return True
    members = TopicMemberService(db)
    seated = (
        await members.joined_topic_ids([room], actor.handle, team=project_team)
        if room is not None
        else await members.joined_ids([topic_id], actor.handle, team=project_team)
    )
    if topic_id in seated:
        return True
    return await _manages_project(db, actor, project_id)


async def _viewer(
    db: AsyncSession,
    resolver: ActorResolver,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    *,
    room: Topic | None = None,
    preload_roster: bool = False,
) -> Actor:
    """The caller, once it is settled that this room's rules are theirs to see."""
    actor = await resolver.resolve(project_id=project_id, topic_id=topic_id)
    if preload_roster and room is not None:
        await TopicMemberService(db)._repo.list_for_topic(room.id)
    await resolver.authorize_topic(
        actor, project_id=project_id, topic_id=topic_id, room=room, enforce=True
    )
    if not resolver.on_the_dev_credential(actor) and not await _sees_room(
        db,
        actor,
        project_id,
        topic_id,
        room=room,
    ):
        raise ForbiddenError(say("routinesRoomMemberOnly"))
    return actor


async def _routine_actor(
    db: AsyncSession, resolver: ActorResolver, routine_id: uuid.UUID
) -> tuple[Routine, Actor]:
    row = await RoutineService(db).get(routine_id)
    room = await TopicService(db).get(row.topic_id)
    if room is not None and isinstance(db, AsyncSession):
        db.sync_session.info.setdefault("_pinned_rows", []).append(room)
    actor = await _viewer(db, resolver, row.project_id, row.topic_id, room=room)
    return row, actor


def _may_delete(row: Routine, actor: Actor, *, admin: bool) -> bool:
    """The rule's owner, or a project manager. Never an AI teammate: whoever may
    read a rule may change it, but taking one away from the room is a person's
    call."""
    if not _is_person(actor):
        return False
    return admin or (bool(actor.handle) and actor.handle == row.owner_handle)


async def _require_delete(db: AsyncSession, actor: Actor, row: Routine) -> None:
    if await _can_delete(db, actor, row):
        return
    what = say("routineDelete")
    if _is_person(actor):
        raise ForbiddenError(
            say(
                "routineOwnerOrAdmin",
                title=row.title,
                owner=row.owner_handle,
                what=what,
            )
        )
    raise ForbiddenError(say("routineManagerOnly", what=what))


async def _can_delete(db: AsyncSession, actor: Actor, row: Routine) -> bool:
    return _may_delete(
        row, actor, admin=await _manages_project(db, actor, row.project_id)
    )


async def _present(
    db: AsyncSession,
    actor: Actor,
    rows: list[Routine],
    *,
    admin: bool,
    fresh: bool = False,
) -> list[dict]:
    """The rows as this caller gets them, carrying the two flags the frontend
    draws buttons from: ``can_delete`` (may this caller take it away) and
    ``room_archived`` (its room went away — the rule is not running now, and
    that is the room's fact, not the rule's).

    归档不写规则那一行（结论：规则状态不变，取消归档后从下一个时刻继续），所以
    「已随话题归档停止」只能由房间答，一次问完这一批。

    行上那颗图形还有两档要说：正在排队、上一次没跑成。这两档同样不写规则那一行，
    一起在这里问完（`last_run_status`）。

    「本月花了多少点」（`credits_this_month`）也一样：规则表上不存累计（`spend.py`
    说明了为什么不存），一起在这里对账本问完这一批。窗口是平台口径的一个月
    （`month_start`），和团队额度页的「本月」是同一个起点。
    """
    service = RoutineService(db)
    archived = await service.archived_room_ids([r.topic_id for r in rows])
    latest = {} if fresh else await service.latest_run_statuses([r.id for r in rows])
    month = month_of(datetime.now(UTC))
    run_ids = [r.id for r in rows if r.id in latest]
    credits = (
        await service.credits_by_rule(
            run_ids,
            since=month_start(month),
            until=month_end(month),
        )
        if run_ids
        else {}
    )
    return [
        _routine(
            r,
            can_delete=_may_delete(r, actor, admin=admin),
            room_archived=r.topic_id in archived,
            last_run_status=latest.get(r.id),
            credits_this_month=credits.get(r.id, 0.0),
        )
        for r in rows
    ]


async def _present_one(
    db: AsyncSession,
    actor: Actor,
    row: Routine,
    *,
    admin: bool | None = None,
    fresh: bool = False,
) -> dict:
    if admin is None:
        if not _is_person(actor) or (
            bool(actor.handle) and actor.handle == row.owner_handle
        ):
            admin = False
        else:
            admin = await _manages_project(db, actor, row.project_id)
    return (await _present(db, actor, [row], admin=admin, fresh=fresh))[0]


async def _speaker(db: AsyncSession, actor: Actor, room_id: uuid.UUID) -> str:
    """The handle a caller acts under here: a person, or the room's teammate."""
    if actor.authenticated:
        return actor.handle
    return await TopicMemberService(db).resolve_agent_handle(room_id)


def _person(actor: Actor, what: str) -> None:
    if not _is_person(actor):
        raise ForbiddenError(say("personOnlyAction", what=what))


async def _readable_rules(
    db: AsyncSession,
    resolver: ActorResolver,
    actor: Actor,
    project_id: uuid.UUID,
    rows: list[Routine],
) -> tuple[list[Routine], bool]:
    """The rows of one project the caller may read, and whether it manages it.

    项目总览今天把整个项目的规则都发给每个成员，于是「谁在哪个房间里设了什么」
    是项目里人人可见的。规则是频道的：看得见的只有这个频道里的人，加项目管理员。
    """
    if not settings.authz_enforce_topic_access or resolver.on_the_dev_credential(actor):
        return rows, False
    admin = await _manages_project(db, actor, project_id)
    if admin:
        # Every channel's rules, except a private channel's they are not in.
        rooms = [
            room
            for room in [
                await TopicService(db).get(i) for i in {r.topic_id for r in rows}
            ]
            if room is not None
        ]
        seen = await resolver.readable_topic_ids(
            actor, project_id=project_id, topics=rooms
        )
        return [r for r in rows if r.topic_id in seen], True
    mine = await TopicMemberService(db).joined_ids(
        list({r.topic_id for r in rows}), actor.handle, team=project_team
    )
    return [r for r in rows if r.topic_id in mine], False


@router.get("/projects/{project_id}/routines")
async def list_routines(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    topic: uuid.UUID | None = None,
) -> dict:
    if topic is not None:
        place = await place_or_404(db, topic)
        if place.project_id != project_id:
            raise NotFoundError(say("topicNotFound"))
        actor = await _viewer(db, resolver, project_id, place.room_id)
        rows = await RoutineService(db).list(project_id, topic_id=place.room_id)
    else:
        actor = await resolver.resolve(project_id=project_id)
        await resolver.authorize_project(actor, project_id=project_id)
        rows = await RoutineService(db).list(project_id)
    rows, admin = await _readable_rules(db, resolver, actor, project_id, rows)
    items = await _present(db, actor, rows, admin=admin)
    return ok(page(items, len(items)))


@router.post("/topics/{topic_id}/routines", dependencies=[ROUTE_DECIDES])
async def create_routine(
    topic_id: uuid.UUID, body: RoutineIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    place = await place_or_404(db, topic_id)
    room = place.room or await TopicService(db).get(place.room_id)
    if room is None:
        raise NotFoundError(say("topicNotFound"))
    if isinstance(db, AsyncSession):
        db.sync_session.info.setdefault("_pinned_rows", []).append(room)
    actor = await _viewer(
        db,
        resolver,
        place.project_id,
        topic_id,
        room=room,
        preload_roster=True,
    )
    row = await RoutineService(db).create(
        topic=room,
        by=await _speaker(db, actor, room.id),
        by_agent=not _is_person(actor),
        title=body.title,
        instructions=body.instructions,
        note=body.note,
        trigger=body.trigger,
        spec=body.spec,
        tz=body.timezone,
        owner_handle=body.owner_handle,
        agent_handle=body.agent_handle,
    )
    presented = await _present_one(db, actor, row, fresh=True)
    await db.commit()
    return ok(presented)


@router.get("/routines/{routine_id}")
async def get_routine(
    routine_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _routine_actor(db, resolver, routine_id)
    presented = await _present_one(db, actor, row)
    service = RoutineService(db)
    runs, _ = await service.runs(row.id)
    month = month_of(datetime.now(UTC))
    per_run = (
        await service.credits_by_run(
            runs, since=month_start(month), until=month_end(month)
        )
        if runs
        else {}
    )
    return ok(
        {
            **presented,
            "runs": [_run(r, credits=per_run.get(r.id)) for r in runs],
        }
    )


@router.patch("/routines/{routine_id}", dependencies=[ROUTE_DECIDES])
async def update_routine(
    routine_id: uuid.UUID, body: RoutinePatch, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _routine_actor(db, resolver, routine_id)
    admin = (
        False
        if not _is_person(actor)
        or (bool(actor.handle) and actor.handle == row.owner_handle)
        else await _manages_project(db, actor, row.project_id)
    )
    row = await RoutineService(db).update(
        row, changes=body.model_dump(exclude_unset=True)
    )
    presented = await _present_one(db, actor, row, admin=admin)
    await db.commit()
    return ok(presented)


@router.post("/routines/{routine_id}/pause", dependencies=[ROUTE_DECIDES])
async def pause_routine(
    routine_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _routine_actor(db, resolver, routine_id)
    admin = (
        False
        if not _is_person(actor)
        or (bool(actor.handle) and actor.handle == row.owner_handle)
        else await _manages_project(db, actor, row.project_id)
    )
    # 看得到就改得动，暂停也一样：暂停停下的是执行，不是新工作。
    row = await RoutineService(db).pause(row)
    presented = await _present_one(db, actor, row, admin=admin)
    await db.commit()
    return ok(presented)


@router.post("/routines/{routine_id}/resume", dependencies=[ROUTE_DECIDES])
async def resume_routine(
    routine_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _routine_actor(db, resolver, routine_id)
    # A teammate may pause a rule, never start one again: what it paused, a
    # person decides to resume.
    _person(actor, say("routineResume"))
    admin = (
        False
        if bool(actor.handle) and actor.handle == row.owner_handle
        else await _manages_project(db, actor, row.project_id)
    )
    row = await RoutineService(db).resume(row)
    presented = await _present_one(db, actor, row, admin=admin)
    await db.commit()
    return ok(presented)


@router.post("/routines/{routine_id}/run-now", dependencies=[ROUTE_DECIDES])
async def run_routine_now(
    routine_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    # deferred-import: tests patch this name on app.api.deps
    from app.api.deps import get_chat_service, get_work_runner

    row, actor = await _routine_actor(db, resolver, routine_id)
    run = await RoutineService(db).run_now(
        row, by=await _speaker(db, actor, row.topic_id)
    )
    await db.commit()
    chat = get_chat_service()
    if run.message_id is not None:
        await routines.publish_run_messages(
            chat.session_factory, [run.message_id], new=True
        )
    await routines.dispatch_pending(
        chat.session_factory, chat=chat, runner=get_work_runner()
    )
    return ok(_run(run))


@router.delete("/routines/{routine_id}", dependencies=[ROUTE_DECIDES])
async def delete_routine(
    routine_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _routine_actor(db, resolver, routine_id)
    _person(actor, say("routineDelete"))
    await _require_delete(db, actor, row)
    await RoutineService(db).delete(row, by=actor.handle)
    await db.commit()
    return ok({"deleted": str(routine_id)})


@router.get("/routines/{routine_id}/runs")
async def list_runs(
    routine_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    failed_only: bool = Query(False),
) -> dict:
    """这条规则的一页执行记录。行里展开时读最近几次，「看全部」那一页翻着读。

    `total` 是**筛过之后的全部**：翻页器上那句「共 N 次」说的是它。

    每一行带它自己本月的点数（`credits`），顶上另外给这条规则本月的合计
    （`credits_this_month`）—— 合计是整条规则的，不是这一页的：读者要看的是「这条
    规则这个月花了多少」，翻到第几页都该是同一个数（`spend.credits_by_rule`）。
    """
    row, _ = await _routine_actor(db, resolver, routine_id)
    service = RoutineService(db)
    items, total = await service.runs(
        row.id, offset=offset, limit=limit, failed_only=failed_only
    )
    month = month_of(datetime.now(UTC))
    window = {"since": month_start(month), "until": month_end(month)}
    per_run = await service.credits_by_run(items, **window) if items else {}
    whole = (
        {}
        if (not failed_only and total == 0)
        else await service.credits_by_rule([row.id], **window)
    )
    return ok(
        {
            **page([_run(r, credits=per_run.get(r.id)) for r in items], total),
            "credits_this_month": whole.get(row.id, 0.0),
        }
    )


@router.post("/routine-runs/{run_id}/report", dependencies=[ROUTE_DECIDES])
async def report_run(
    run_id: uuid.UUID, body: ReportIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    run = await db.get(RoutineRun, run_id)
    if run is None:
        raise NotFoundError(say("routineRunNotFound"))
    routine, actor = await _routine_actor(db, resolver, run.routine_id)
    run = await RoutineService(db).report(
        run,
        by=await _speaker(db, actor, routine.topic_id),
        status=body.status,
        summary=body.summary,
        outputs=body.outputs,
    )
    await db.commit()
    return ok(_run(run))


@router.get(
    "/projects/{project_id}/routine-templates",
    **typed_response(RoutineTemplateListOut),
)
async def list_routine_templates(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """这个项目里存下来的填表起点，最近存的在最前面。

    项目的一份，谁读得到这个项目就读得到 —— 不看房间名册：模板不属于哪个房间，
    它是一张还没落到任何房间里的表。
    """
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    rows = await RoutineTemplateService(db).list(project_id)
    return ok(page([_template(r) for r in rows], len(rows)))


@router.post(
    "/projects/{project_id}/routine-templates",
    dependencies=[ROUTE_DECIDES],
    **typed_response(RoutineTemplateOut),
)
async def create_routine_template(
    project_id: uuid.UUID,
    body: RoutineTemplateIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    row = await RoutineTemplateService(db).create(
        project_id=project_id,
        title=body.title,
        goal=body.goal,
        note=body.note,
        trigger=body.trigger,
        spec=body.spec,
    )
    await db.commit()
    return ok(_template(row))


@router.patch(
    "/routine-templates/{template_id}",
    dependencies=[ROUTE_DECIDES],
    **typed_response(RoutineTemplateOut),
)
async def update_routine_template(
    template_id: uuid.UUID,
    body: RoutineTemplateIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """改这一条模板本身：名字、目标、补充说明、触发方式、时间都落在原来这一行上。

    和存一条同一套校验、同一份权限（项目的一份，读得到就改得动）。已经照它建出来的
    规则不动：模板是底稿，建出来的各是各的。
    """
    row = await RoutineTemplateService(db).get(template_id)
    actor = await resolver.resolve(project_id=row.project_id)
    await resolver.authorize_project(actor, project_id=row.project_id)
    row = await RoutineTemplateService(db).update(
        row,
        title=body.title,
        goal=body.goal,
        note=body.note,
        trigger=body.trigger,
        spec=body.spec,
    )
    await db.commit()
    return ok(_template(row))


@router.delete(
    "/routine-templates/{template_id}",
    dependencies=[ROUTE_DECIDES],
    **typed_response(RoutineTemplateDeleted),
)
async def delete_routine_template(
    template_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row = await RoutineTemplateService(db).get(template_id)
    actor = await resolver.resolve(project_id=row.project_id)
    await resolver.authorize_project(actor, project_id=row.project_id)
    await RoutineTemplateService(db).delete(row)
    await db.commit()
    return ok({"deleted": str(template_id)})
