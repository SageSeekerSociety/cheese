"""Space routes (spaces + their task templates)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, page
from app.core.db import get_db
from app.domain.space.schemas import SpaceCreate, SpaceOut
from app.domain.space.services import SpaceService
from app.domain.task.schemas import TaskTemplateCreate, TaskTemplateOut
from app.domain.task.services import TaskTemplateService

router = APIRouter(prefix="/api/spaces", tags=["spaces"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post("")
async def create_space(body: SpaceCreate, db: DbSession) -> dict:
    space = await SpaceService(db).create(
        name=body.name, kind=body.kind, description=body.description
    )
    return ok(SpaceOut.model_validate(space).model_dump(mode="json"))


@router.get("")
async def list_spaces(db: DbSession) -> dict:
    spaces, total = await SpaceService(db).list_all()
    items = [SpaceOut.model_validate(s).model_dump(mode="json") for s in spaces]
    return ok(page(items, total))


@router.get("/{space_id}")
async def get_space(space_id: uuid.UUID, db: DbSession) -> dict:
    space = await SpaceService(db).get_or_404(space_id)
    return ok(SpaceOut.model_validate(space).model_dump(mode="json"))


@router.post("/{space_id}/templates")
async def create_template(
    space_id: uuid.UUID, body: TaskTemplateCreate, db: DbSession
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
async def list_space_templates(space_id: uuid.UUID, db: DbSession) -> dict:
    templates, total = await TaskTemplateService(db).list_for_space(space_id)
    items = [
        TaskTemplateOut.model_validate(t).model_dump(mode="json") for t in templates
    ]
    return ok(page(items, total))


@router.post("/{space_id}/templates/{template_id}/publish")
async def publish_template(
    space_id: uuid.UUID, template_id: uuid.UUID, db: DbSession
) -> dict:
    """题目发布: list the template on the 匹配市场 (spec §13 阶段 6)."""
    template = await TaskTemplateService(db).set_published(
        space_id=space_id, template_id=template_id, published=True
    )
    return ok(TaskTemplateOut.model_validate(template).model_dump(mode="json"))


@router.post("/{space_id}/templates/{template_id}/unpublish")
async def unpublish_template(
    space_id: uuid.UUID, template_id: uuid.UUID, db: DbSession
) -> dict:
    """Take the template off the 匹配市场 (existing links are untouched)."""
    template = await TaskTemplateService(db).set_published(
        space_id=space_id, template_id=template_id, published=False
    )
    return ok(TaskTemplateOut.model_validate(template).model_dump(mode="json"))
