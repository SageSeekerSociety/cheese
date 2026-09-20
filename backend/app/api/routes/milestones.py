"""Milestone + calendar routes (spec §7.2 时间维度)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_work_runner
from app.api.response import ok, page
from app.core.db import get_db
from app.domain.idempotency import store as idem
from app.domain.idempotency.keys import action_key
from app.domain.milestone.schemas import (
    MilestoneCreate,
    MilestoneOut,
    MilestoneUpdate,
)
from app.domain.milestone.services import MilestoneService

router = APIRouter(prefix="", tags=["milestones"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post("/projects/{project_id}/milestones")
async def create_milestone(
    project_id: uuid.UUID, body: MilestoneCreate, db: DbSession
) -> dict:
    # 重发幂等 (④). A milestone is project-scoped but `cheese milestone` is
    # always run from inside a topic's turn, and that topic is what the body
    # carries as `source_topic_id` — so it is also what names the continuation
    # to dedup against. No source topic (a human pinning one in the UI) → no
    # continuation → no dedup, same rule as everywhere else.
    continuation = (
        get_work_runner().continuation_for(body.source_topic_id)
        if body.source_topic_id
        else None
    )
    key = (
        action_key(continuation, "milestone", project_id, body.title, body.due_date)
        if continuation
        else None
    )
    if key is not None and not await idem.claim(
        db, key, action="milestone", scope_id=str(project_id)
    ):
        prior = await idem.stored_result(db, key)
        return ok(prior or {"skipped": True})
    milestone = await MilestoneService(db).create(
        project_id=project_id,
        title=body.title,
        description=body.description,
        due_date=body.due_date,
        source_topic_id=body.source_topic_id,
        auto_pinned=body.auto_pinned,
    )
    out = MilestoneOut.model_validate(milestone).model_dump(mode="json")
    if key is not None:
        await idem.record_result(db, key, out)
    return ok(out)


@router.get("/projects/{project_id}/milestones")
async def list_milestones(project_id: uuid.UUID, db: DbSession) -> dict:
    milestones, total = await MilestoneService(db).list_for_project(project_id)
    items = [MilestoneOut.model_validate(m).model_dump(mode="json") for m in milestones]
    return ok(page(items, total))


@router.get("/projects/{project_id}/calendar")
async def project_calendar(project_id: uuid.UUID, db: DbSession) -> dict:
    milestones = await MilestoneService(db).calendar(project_id)
    items = [MilestoneOut.model_validate(m).model_dump(mode="json") for m in milestones]
    return ok(page(items, len(items)))


@router.put("/milestones/{milestone_id}")
async def update_milestone(
    milestone_id: uuid.UUID, body: MilestoneUpdate, db: DbSession
) -> dict:
    milestone = await MilestoneService(db).update(
        milestone_id,
        title=body.title,
        description=body.description,
        due_date=body.due_date,
        status=body.status,
        due_date_set="due_date" in body.model_fields_set,
    )
    return ok(MilestoneOut.model_validate(milestone).model_dump(mode="json"))


@router.delete("/milestones/{milestone_id}")
async def delete_milestone(milestone_id: uuid.UUID, db: DbSession) -> dict:
    await MilestoneService(db).delete(milestone_id)
    return ok(None)
