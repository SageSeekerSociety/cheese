"""Task-template routes under a Space.

The Space entity itself is served by main-cheese's ``spaces.py`` (the real 知是
机构 with categories/ranks/etc.); this module only adds cheesex's task-template
market on top of a space (spec §4.2). It used to also expose a duplicate cheesex
Space CRUD stub under ``/api/spaces`` — that collided with main's routes and hid
the real spaces, so it was removed (fusion unify P1b)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, page
from app.core.db import get_db
from app.domain.cx_task.schemas import TaskTemplateCreate, TaskTemplateOut
from app.domain.cx_task.services import TaskTemplateService

router = APIRouter(prefix="/api/spaces", tags=["spaces"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post("/{space_id}/templates")
async def create_template(
    space_id: int, body: TaskTemplateCreate, db: DbSession
) -> dict:
    template = await TaskTemplateService(db).create(
        space_id=space_id,
        name=body.name,
        description=body.description,
        resource_pack=body.resource_pack,
        conditions=body.conditions,
        default_role=body.default_role,
    )
    return ok(TaskTemplateOut.model_validate(template).model_dump(mode="json"))


@router.get("/{space_id}/templates")
async def list_space_templates(space_id: int, db: DbSession) -> dict:
    templates, total = await TaskTemplateService(db).list_for_space(space_id)
    items = [
        TaskTemplateOut.model_validate(t).model_dump(mode="json") for t in templates
    ]
    return ok(page(items, total))


@router.post("/{space_id}/templates/{template_id}/publish")
async def publish_template(
    space_id: int, template_id: uuid.UUID, db: DbSession
) -> dict:
    """题目发布: list the template on the 匹配市场 (spec §13 阶段 6)."""
    template = await TaskTemplateService(db).set_published(
        space_id=space_id, template_id=template_id, published=True
    )
    return ok(TaskTemplateOut.model_validate(template).model_dump(mode="json"))


@router.post("/{space_id}/templates/{template_id}/unpublish")
async def unpublish_template(
    space_id: int, template_id: uuid.UUID, db: DbSession
) -> dict:
    """Take the template off the 匹配市场 (existing links are untouched)."""
    template = await TaskTemplateService(db).set_published(
        space_id=space_id, template_id=template_id, published=False
    )
    return ok(TaskTemplateOut.model_validate(template).model_dump(mode="json"))
