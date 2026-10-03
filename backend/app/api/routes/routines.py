"""Routines: standing work on a clock or on a project event.

An AI teammate may draft or edit one; only a person confirms it, resumes it or
runs it by hand, because each of those is what makes work happen unattended.

**Who a rule belongs to.** Seeing one is the room's business: the people on that
room's roster, plus whoever manages the project. Being a member of the project
itself is NOT enough — a rule names what one room does on a clock, and the other
rooms of the project have no business reading who set what there. Acting on one
(confirming, pausing, resuming, running by hand, deleting, and a person's edit)
is narrower still: the person it notifies (``owner_handle``) or a project
manager. Every response carries ``can_manage`` so the frontend knows which
buttons to draw.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.response import ok, page
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.identity.actor import Actor
from app.domain.membership.services import MemberService
from app.domain.routine import service as routines
from app.domain.routine.models import Routine, RoutineRun
from app.domain.routine.service import RoutineService, describe_trigger
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="", tags=["routines"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


class RoutineIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    instructions: str = Field(min_length=1)
    context_scope: str = ""
    output_dir: str = ""
    trigger: str = "schedule"
    spec: dict = Field(default_factory=dict)
    timezone: str = "Asia/Shanghai"
    owner_handle: str | None = None
    agent_handle: str | None = None


class RoutinePatch(BaseModel):
    title: str | None = None
    instructions: str | None = None
    context_scope: str | None = None
    output_dir: str | None = None
    trigger: str | None = None
    spec: dict | None = None
    timezone: str | None = None
    agent_handle: str | None = None


class ReportIn(BaseModel):
    status: str
    summary: str = ""
    outputs: list[str] = Field(default_factory=list)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _routine(
    row: Routine, *, can_manage: bool = False, room_archived: bool = False
) -> dict:
    return {
        "id": str(row.id),
        "can_manage": can_manage,
        "room_archived": room_archived,
        "project_id": str(row.project_id),
        "topic_id": str(row.topic_id),
        "title": row.title,
        "instructions": row.instructions,
        "context_scope": row.context_scope,
        "output_dir": row.output_dir,
        "trigger": row.trigger,
        "trigger_text": describe_trigger(row),
        "spec": row.spec,
        "timezone": row.timezone,
        "state": row.state,
        "agent_handle": row.agent_handle,
        "owner_handle": row.owner_handle,
        "proposed_by": row.proposed_by,
        "confirmed_by": row.confirmed_by,
        "confirmed_at": _iso(row.confirmed_at),
        "next_run_at": _iso(row.next_run_at),
        "revision": row.revision,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _run(row: RoutineRun) -> dict:
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
    }


def _is_person(actor: Actor) -> bool:
    return actor.via == "token"


async def _manages_project(
    db: AsyncSession, actor: Actor, project_id: uuid.UUID
) -> bool:
    """Is ``actor`` a project manager — its owner, or an owner/admin of the team
    the project belongs to?

    Not a new judgment: ``MemberService.manages`` is the same one the project's
    own routes ask (``app/domain/membership/services.py``). Credentials come
    first: a handle that arrived through the deprecated body/param fallback is
    whatever the caller typed, and anyone can type the owner's handle.
    """
    if not actor.authenticated:
        return False
    return await MemberService(db).manages(project_id, actor.handle)


async def _sees_room(
    db: AsyncSession, actor: Actor, project_id: uuid.UUID, topic_id: uuid.UUID
) -> bool:
    """May ``actor`` read what happens in this room: it is on the roster (the
    room's own AI teammate included), or it manages the project.

    Narrower than ``authorize_topic_access``, and deliberately: that policy lets
    anyone in the project into a non-private room, which is right for the room's
    conversation and wrong for the rules of ONE of the project's rooms.
    """
    if not settings.authz_enforce_topic_access:
        return True
    seated = await TopicMemberService(db).topic_ids_for_member([topic_id], actor.handle)
    if topic_id in seated:
        return True
    return await _manages_project(db, actor, project_id)


async def _viewer(
    db: AsyncSession,
    resolver: ActorResolver,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
) -> Actor:
    """The caller, once it is settled that this room's rules are theirs to see."""
    actor = await resolver.resolve(project_id=project_id, topic_id=topic_id)
    await resolver.authorize_topic(
        actor, project_id=project_id, topic_id=topic_id, enforce=True
    )
    if not resolver.on_the_dev_credential(actor) and not await _sees_room(
        db, actor, project_id, topic_id
    ):
        raise ForbiddenError(say("routinesRoomMemberOnly"))
    return actor


async def _routine_actor(
    db: AsyncSession, resolver: ActorResolver, routine_id: uuid.UUID
) -> tuple[Routine, Actor]:
    row = await RoutineService(db).get(routine_id)
    actor = await _viewer(db, resolver, row.project_id, row.topic_id)
    return row, actor


def _may_manage(row: Routine, actor: Actor, *, admin: bool) -> bool:
    """The rule's owner, or a project manager. An AI teammate never: it drafts
    and edits, and a person decides."""
    if not _is_person(actor):
        return False
    return admin or (bool(actor.handle) and actor.handle == row.owner_handle)


async def _require_manage(
    db: AsyncSession, actor: Actor, row: Routine, what: str
) -> None:
    if await _can_manage(db, actor, row):
        return
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


async def _can_manage(db: AsyncSession, actor: Actor, row: Routine) -> bool:
    return _may_manage(
        row, actor, admin=await _manages_project(db, actor, row.project_id)
    )


async def _present(
    db: AsyncSession, actor: Actor, rows: list[Routine], *, admin: bool
) -> list[dict]:
    """The rows as this caller gets them, carrying the two flags the frontend
    draws buttons from: ``can_manage`` (may this caller act on it) and
    ``room_archived`` (its room went away — the rule is not running now, and
    that is the room's fact, not the rule's).

    归档不写规则那一行（结论：规则状态不变，取消归档后从下一个时刻继续），所以
    「已随话题归档停止」只能由房间答，一次问完这一批。
    """
    archived = await RoutineService(db).archived_room_ids([r.topic_id for r in rows])
    return [
        _routine(
            r,
            can_manage=_may_manage(r, actor, admin=admin),
            room_archived=r.topic_id in archived,
        )
        for r in rows
    ]


async def _present_one(db: AsyncSession, actor: Actor, row: Routine) -> dict:
    admin = await _manages_project(db, actor, row.project_id)
    return (await _present(db, actor, [row], admin=admin))[0]


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
    是项目里人人可见的。规则是房间的：看得见的只有它所在房间的名册，加项目管理员。
    """
    if not settings.authz_enforce_topic_access or resolver.on_the_dev_credential(actor):
        return rows, False
    admin = await _manages_project(db, actor, project_id)
    if admin:
        return rows, True
    mine = await TopicMemberService(db).topic_ids_for_member(
        list({r.topic_id for r in rows}), actor.handle
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
        place = await TopicService(db).place_or_404(topic)
        if place.project_id != project_id:
            raise NotFoundError("Topic not found")
        actor = await _viewer(db, resolver, project_id, place.room_id)
        rows = await RoutineService(db).list(project_id, topic_id=place.room_id)
    else:
        actor = await resolver.resolve(project_id=project_id)
        await resolver.authorize_project(actor, project_id=project_id)
        rows = await RoutineService(db).list(project_id)
    rows, admin = await _readable_rules(db, resolver, actor, project_id, rows)
    items = await _present(db, actor, rows, admin=admin)
    return ok(page(items, len(items)))


@router.post("/topics/{topic_id}/routines")
async def create_routine(
    topic_id: uuid.UUID, body: RoutineIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    place = await TopicService(db).place_or_404(topic_id)
    actor = await _viewer(db, resolver, place.project_id, topic_id)
    room = await TopicService(db).get(place.room_id)
    if room is None:
        raise NotFoundError("Topic not found")
    row = await RoutineService(db).create(
        topic=room,
        by=await _speaker(db, actor, room.id),
        by_agent=not _is_person(actor),
        title=body.title,
        instructions=body.instructions,
        context_scope=body.context_scope,
        output_dir=body.output_dir,
        trigger=body.trigger,
        spec=body.spec,
        tz=body.timezone,
        owner_handle=body.owner_handle,
        agent_handle=body.agent_handle,
    )
    presented = await _present_one(db, actor, row)
    await db.commit()
    return ok(presented)


@router.get("/routines/{routine_id}")
async def get_routine(
    routine_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _routine_actor(db, resolver, routine_id)
    presented = await _present_one(db, actor, row)
    runs = await RoutineService(db).runs(row.id)
    return ok({**presented, "runs": [_run(r) for r in runs]})


@router.patch("/routines/{routine_id}")
async def update_routine(
    routine_id: uuid.UUID, body: RoutinePatch, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _routine_actor(db, resolver, routine_id)
    if _is_person(actor):
        await _require_manage(db, actor, row, say("routineEdit"))
    row = await RoutineService(db).update(
        row,
        by_agent=not _is_person(actor),
        changes=body.model_dump(exclude_unset=True),
    )
    presented = await _present_one(db, actor, row)
    await db.commit()
    return ok(presented)


@router.post("/routines/{routine_id}/confirm")
async def confirm_routine(
    routine_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _routine_actor(db, resolver, routine_id)
    _person(actor, say("routineConfirmEnable"))
    await _require_manage(db, actor, row, say("routineConfirmEnable"))
    row = await RoutineService(db).confirm(row, by=actor.handle)
    presented = await _present_one(db, actor, row)
    await db.commit()
    return ok(presented)


@router.post("/routines/{routine_id}/pause")
async def pause_routine(
    routine_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _routine_actor(db, resolver, routine_id)
    # 芝士今天就能暂停（人写的规则也暂停），这一档不变：暂停停下的是执行，不
    # 是新工作；其余的操作仍要人来做。
    if _is_person(actor):
        await _require_manage(db, actor, row, say("routinePause"))
    row = await RoutineService(db).pause(row)
    presented = await _present_one(db, actor, row)
    await db.commit()
    return ok(presented)


@router.post("/routines/{routine_id}/resume")
async def resume_routine(
    routine_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _routine_actor(db, resolver, routine_id)
    _person(actor, say("routineResume"))
    await _require_manage(db, actor, row, say("routineResume"))
    row = await RoutineService(db).resume(row)
    presented = await _present_one(db, actor, row)
    await db.commit()
    return ok(presented)


@router.post("/routines/{routine_id}/run-now")
async def run_routine_now(
    routine_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    from app.api.deps import get_chat_service, get_work_runner

    row, actor = await _routine_actor(db, resolver, routine_id)
    _person(actor, say("routineRunNow"))
    await _require_manage(db, actor, row, say("routineRunNow"))
    run = await RoutineService(db).run_now(row, by=actor.handle)
    await db.commit()
    chat = get_chat_service()
    await routines.dispatch_pending(
        chat.session_factory, chat=chat, runner=get_work_runner()
    )
    return ok(_run(run))


@router.delete("/routines/{routine_id}")
async def delete_routine(
    routine_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _routine_actor(db, resolver, routine_id)
    _person(actor, say("routineDelete"))
    await _require_manage(db, actor, row, say("routineDelete"))
    await RoutineService(db).delete(row)
    await db.commit()
    return ok({"deleted": str(routine_id)})


@router.get("/routines/{routine_id}/runs")
async def list_runs(
    routine_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, _ = await _routine_actor(db, resolver, routine_id)
    items = [_run(r) for r in await RoutineService(db).runs(row.id)]
    return ok(page(items, len(items)))


@router.post("/routine-runs/{run_id}/report")
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
