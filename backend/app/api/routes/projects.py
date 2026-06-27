"""Project routes."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import ValidationError
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.project.schemas import (
    ProjectCreate,
    ProjectOut,
    TaskLinkCreate,
    TaskLinkOut,
)
from app.domain.project.services import ProjectService
from app.domain.topic.schemas import TopicOut
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/api/projects", tags=["projects"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post("")
async def create_project(body: ProjectCreate, db: DbSession) -> dict:
    project = await ProjectService(db).create(
        name=body.name,
        owner_handle=body.owner_handle,
        ai_mode=body.ai_mode,
        expert_role=body.expert_role,
    )
    return ok(ProjectOut.model_validate(project).model_dump(mode="json"))


@router.get("")
async def list_projects(db: DbSession) -> dict:
    projects, total = await ProjectService(db).list_all()
    items = [ProjectOut.model_validate(p).model_dump(mode="json") for p in projects]
    return ok(page(items, total))


@router.get("/{project_id}")
async def get_project(project_id: uuid.UUID, db: DbSession) -> dict:
    project = await ProjectService(db).get_or_404(project_id)
    return ok(ProjectOut.model_validate(project).model_dump(mode="json"))


@router.post("/{project_id}/tasks")
async def link_task(project_id: uuid.UUID, body: TaskLinkCreate, db: DbSession) -> dict:
    link = await ProjectService(db).link_task(
        project_id=project_id, task_id=body.task_id
    )
    return ok(TaskLinkOut.model_validate(link).model_dump(mode="json"))


@router.get("/{project_id}/tasks")
async def list_linked_tasks(project_id: uuid.UUID, db: DbSession) -> dict:
    links, total = await ProjectService(db).list_links(project_id)
    items = [TaskLinkOut.model_validate(link).model_dump(mode="json") for link in links]
    return ok(page(items, total))


@router.delete("/{project_id}/tasks/{task_id}")
async def unlink_task(
    project_id: uuid.UUID, task_id: uuid.UUID, db: DbSession
) -> dict:
    """退出 Task 协议 (§4): break the project↔task link."""
    await ProjectService(db).unlink_task(project_id=project_id, task_id=task_id)
    return ok({"unlinked": True})


@router.get("/{project_id}/decisions")
async def list_decisions(project_id: uuid.UUID, db: DbSession) -> dict:
    """决策记录 (spec §7.1): project-wide decision blocks, each traceable to its
    source topic via topic_id."""
    await ProjectService(db).get_or_404(project_id)
    blocks = await BlockRepository(db).list_by_kind_for_project(
        project_id, BlockKind.decision
    )
    items = [BlockOut.model_validate(b).model_dump(mode="json") for b in blocks]
    return ok(page(items, len(items)))


@router.post("/{project_id}/memory")
async def add_memory(project_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """记入项目记忆 (spec §8.4) — used by the `cheese remember` CLI."""
    from app.domain.memory.models import MemoryScope
    from app.domain.memory.store import DbMemoryStore

    await ProjectService(db).get_or_404(project_id)
    content = (body.get("content") or "").strip()
    if not content:
        raise ValidationError("content 不能为空")
    await DbMemoryStore(db).remember(MemoryScope.project, str(project_id), content)
    return ok({"remembered": True})


@router.get("/{project_id}/private-chat")
async def get_private_chat(
    project_id: uuid.UUID, user_handle: str, db: DbSession
) -> dict:
    """Get-or-create the member's 1:1 private chat with 芝士 (spec §1)."""
    topic = await TopicService(db).get_or_create_private(
        project_id=project_id, user_handle=user_handle
    )
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))
