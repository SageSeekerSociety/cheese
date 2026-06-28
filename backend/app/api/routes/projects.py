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
    """记入记忆 — used by the `cheese remember` CLI. Defaults to project memory
    (spec §8.4); with scope="user"+owner it writes that member's personal memory
    (private chat, spec §8.4 个人记忆跟着人走)."""
    from app.domain.memory.models import MemoryScope
    from app.domain.memory.store import DbMemoryStore

    await ProjectService(db).get_or_404(project_id)
    content = (body.get("content") or "").strip()
    if not content:
        raise ValidationError("content 不能为空")
    if (body.get("scope") or "project") == "user":
        owner = (body.get("owner") or "").strip()
        if not owner:
            raise ValidationError("owner 不能为空（个人记忆需要 owner）")
        await DbMemoryStore(db).remember(MemoryScope.user, owner, content)
    else:
        await DbMemoryStore(db).remember(MemoryScope.project, str(project_id), content)
    return ok({"remembered": True})


@router.post("/{project_id}/mention")
async def mention_member(project_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """@点名某人(带回执)— used by `cheese mention`. Resolves a name/handle against
    the roster: on a match, sends a strong notification and returns the resolved
    member; on no match, returns the candidate list so 芝士 can self-correct."""
    import uuid as _uuid

    from app.domain.notification.models import NotifKind, NotifLevel
    from app.domain.notification.services import NotificationService
    from app.domain.project.repositories import ProjectRepository

    await ProjectService(db).get_or_404(project_id)
    name = (body.get("name") or "").strip()
    if not name:
        raise ValidationError("name 不能为空")
    roster = await ProjectRepository(db).list_members(project_id)
    match = next(
        (m for m in roster if m["name"] == name or m["handle"] == name), None
    )
    if match is None:
        candidates = [{"handle": m["handle"], "name": m["name"]} for m in roster]
        return ok({"ok": False, "reason": "no_match", "candidates": candidates})
    topic_raw = body.get("topic_id")
    topic_id = _uuid.UUID(topic_raw) if topic_raw else None
    await NotificationService(db).create(
        project_id=project_id,
        level=NotifLevel.strong,
        kind=NotifKind.mention,
        title=f"芝士 @了你：{name}",
        body=(body.get("reason") or "")[:200],
        target_handle=match["handle"],
        topic_id=topic_id,
    )
    return ok({"ok": True, "handle": match["handle"], "name": match["name"]})


@router.get("/{project_id}/private-chat")
async def get_private_chat(
    project_id: uuid.UUID, user_handle: str, db: DbSession
) -> dict:
    """Get-or-create the member's 1:1 private chat with 芝士 (spec §1)."""
    topic = await TopicService(db).get_or_create_private(
        project_id=project_id, user_handle=user_handle
    )
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))
