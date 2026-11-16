"""The platform admin's queue of 题目 waiting for review, across every space.

A space's own queue (``GET /tasks?approved=NONE``, ``PATCH /tasks/{id}``) is
for whoever teaches that space. A platform admin reviews from here as well,
in spaces where they hold no seat: the list reads every space, and a decision
is written the way the space's own reviewers write it (``TaskService.review``).
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.admin_deps import PlatformAdminDep
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.task_deps import get_task_service
from app.api.task_serialization import (
    _enrich_task_models,
    _enrich_task_submission_schema,
    _enrich_task_topics,
    _task_to_api_model,
)
from app.api.write_access import PLATFORM_ADMIN
from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.space import task_review_queue as review_queue
from app.domain.task.services import TaskService
from app.domain.user.services import user_by_handle

router = APIRouter(prefix="/admin/tasks", tags=["admin"], dependencies=[PLATFORM_ADMIN])


class QueuePage(BaseModel):
    pageStart: str
    pageSize: int
    hasMore: bool
    nextStart: str | None
    total: int


class UnreviewedTasksOut(BaseModel):
    """A page of the queue; each task in the shape ``GET /tasks`` lists it."""

    tasks: list[dict]
    page: QueuePage


class ReviewedTaskOut(BaseModel):
    task: dict


class ReviewTaskRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    approved: Literal["APPROVED", "DISAPPROVED"]
    reject_reason: str | None = Field(default=None, alias="rejectReason")


async def _task_models(db, tasks) -> list[dict]:
    """The tasks in the shape a space's queue lists them, each with its space."""
    items = [_task_to_api_model(task) for task in tasks]
    by_space: dict[int, list[dict]] = {}
    for item, task in zip(items, tasks, strict=True):
        by_space.setdefault(task.space_id, []).append(item)
    for space_id, group in by_space.items():
        await _enrich_task_models(db, group, space_id=space_id)
    await _enrich_task_topics(db, items)
    await _enrich_task_submission_schema(db, items)
    return items


@router.get("", **typed_response(UnreviewedTasksOut))
async def list_unreviewed_tasks(
    db: DbSession,
    handle: PlatformAdminDep,
    pageStart: str | None = Query(default=None),
    pageSize: int = Query(default=20, ge=1, le=100),
) -> dict:
    offset = int(pageStart) if pageStart and pageStart.isdigit() else 0
    tasks = await review_queue.unreviewed_tasks(db, limit=pageSize, offset=offset)
    items = await _task_models(db, tasks)
    total = await review_queue.count_unreviewed(db)
    has_more = offset + len(items) < total
    return ok(
        {
            "tasks": items,
            "page": {
                "pageStart": pageStart or "",
                "pageSize": len(items),
                "hasMore": has_more,
                "nextStart": str(offset + len(items)) if has_more and items else None,
                "total": total,
            },
        }
    )


@router.post("/{taskId}/review", **typed_response(ReviewedTaskOut))
async def review_unreviewed_task(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    body: ReviewTaskRequest,
    db: DbSession,
    handle: PlatformAdminDep,
    service: Annotated[TaskService, Depends(get_task_service)],
) -> dict:
    task = await service.get_task(task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)
    # The decision is recorded against an account (``reviewed_by``); a handle
    # on the admin list with no account behind it has nobody to record.
    reviewer = await user_by_handle(db, handle)
    if reviewer is None:
        raise ForbiddenError(say("signInRequired"))
    await service.review(
        task,
        approved=body.approved,
        reject_reason=body.reject_reason,
        reviewer_id=reviewer.id,
    )
    await service.save(task)
    # Commit before answering, for the reason ``update_task`` gives: a client
    # told a task is approved may open its board on the very next request.
    await db.commit()
    return ok({"task": (await _task_models(db, [task]))[0]})
