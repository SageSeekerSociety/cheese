"""Routines: standing work on a clock or on a project event.

An AI teammate may draft or edit one; only a person confirms it, resumes it or
runs it by hand, because each of those is what makes work happen unattended.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import ForbiddenError, NotFoundError
from app.domain.identity.actor import Actor
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


def _routine(row: Routine) -> dict:
    return {
        "id": str(row.id),
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


async def _in_room(
    resolver: ActorResolver, project_id: uuid.UUID, topic_id: uuid.UUID
) -> Actor:
    actor = await resolver.resolve(
        fallback_handle=None, project_id=project_id, topic_id=topic_id
    )
    await resolver.authorize_topic(
        actor, project_id=project_id, topic_id=topic_id, enforce=True
    )
    return actor


async def _routine_actor(
    db: AsyncSession, resolver: ActorResolver, routine_id: uuid.UUID
) -> tuple[Routine, Actor]:
    row = await RoutineService(db).get(routine_id)
    actor = await _in_room(resolver, row.project_id, row.topic_id)
    return row, actor


async def _speaker(db: AsyncSession, actor: Actor, room_id: uuid.UUID) -> str:
    """The handle a caller acts under here: a person, or the room's teammate."""
    if actor.authenticated:
        return actor.handle
    return await TopicMemberService(db).resolve_agent_handle(room_id)


def _person(actor: Actor, what: str) -> None:
    if not _is_person(actor):
        raise ForbiddenError(f"{what}要由人来做，AI 队友只能起草和修改")


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
        await _in_room(resolver, project_id, place.room_id)
        rows = await RoutineService(db).list(project_id, topic_id=place.room_id)
    else:
        actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
        await resolver.authorize_project(actor, project_id=project_id)
        rows = await RoutineService(db).list(project_id)
    items = [_routine(r) for r in rows]
    return ok(page(items, len(items)))


@router.post("/topics/{topic_id}/routines")
async def create_routine(
    topic_id: uuid.UUID, body: RoutineIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    place = await TopicService(db).place_or_404(topic_id)
    actor = await _in_room(resolver, place.project_id, topic_id)
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
    await db.commit()
    return ok(_routine(row))


@router.get("/routines/{routine_id}")
async def get_routine(
    routine_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, _ = await _routine_actor(db, resolver, routine_id)
    runs = await RoutineService(db).runs(row.id)
    return ok({**_routine(row), "runs": [_run(r) for r in runs]})


@router.patch("/routines/{routine_id}")
async def update_routine(
    routine_id: uuid.UUID, body: RoutinePatch, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _routine_actor(db, resolver, routine_id)
    row = await RoutineService(db).update(
        row,
        by_agent=not _is_person(actor),
        changes=body.model_dump(exclude_unset=True),
    )
    await db.commit()
    return ok(_routine(row))


@router.post("/routines/{routine_id}/confirm")
async def confirm_routine(
    routine_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _routine_actor(db, resolver, routine_id)
    _person(actor, "确认启用")
    row = await RoutineService(db).confirm(row, by=actor.handle)
    await db.commit()
    return ok(_routine(row))


@router.post("/routines/{routine_id}/pause")
async def pause_routine(
    routine_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, _ = await _routine_actor(db, resolver, routine_id)
    row = await RoutineService(db).pause(row)
    await db.commit()
    return ok(_routine(row))


@router.post("/routines/{routine_id}/resume")
async def resume_routine(
    routine_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _routine_actor(db, resolver, routine_id)
    _person(actor, "恢复执行")
    row = await RoutineService(db).resume(row)
    await db.commit()
    return ok(_routine(row))


@router.post("/routines/{routine_id}/run-now")
async def run_routine_now(
    routine_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    from app.api.deps import get_chat_service, get_work_runner

    row, actor = await _routine_actor(db, resolver, routine_id)
    _person(actor, "立即执行")
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
    _person(actor, "删除")
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
        raise NotFoundError("没有这次执行")
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
