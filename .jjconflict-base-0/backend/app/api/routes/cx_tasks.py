"""Task Template & Task routes (/api/templates/... and /api/tasks/...)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, page
from app.core.db import get_db
from app.domain.cx_task.schemas import (
    TaskCreate,
    TaskOut,
    TaskTemplateOut,
)
from app.domain.cx_task.services import TaskService, TaskTemplateService

router = APIRouter(prefix="", tags=["tasks"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("/api/templates/{template_id}")
async def get_template(template_id: uuid.UUID, db: DbSession) -> dict:
    template = await TaskTemplateService(db).get_or_404(template_id)
    return ok(TaskTemplateOut.model_validate(template).model_dump(mode="json"))


@router.post("/api/templates/{template_id}/tasks")
async def create_task(template_id: uuid.UUID, body: TaskCreate, db: DbSession) -> dict:
    task = await TaskService(db).create(
        template_id=template_id, title=body.title, description=body.description
    )
    return ok(TaskOut.model_validate(task).model_dump(mode="json"))


@router.get("/api/templates/{template_id}/tasks")
async def list_template_tasks(template_id: uuid.UUID, db: DbSession) -> dict:
    tasks, total = await TaskService(db).list_for_template(template_id)
    items = [TaskOut.model_validate(t).model_dump(mode="json") for t in tasks]
    return ok(page(items, total))


@router.get("/api/tasks/{task_id}")
async def get_task(task_id: uuid.UUID, db: DbSession) -> dict:
    task = await TaskService(db).get_or_404(task_id)
    return ok(TaskOut.model_validate(task).model_dump(mode="json"))
