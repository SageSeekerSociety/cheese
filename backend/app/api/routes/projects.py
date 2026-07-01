"""Project routes."""

import re
import uuid
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_profile_registry
from app.api.response import ok, page
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import NotFoundError, ValidationError
from app.domain.agent.market import compute_default_name, compute_selectable
from app.domain.agent.profiles import ProfileRegistry
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.project.repositories import ProjectRepository
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
Registry = Annotated[ProfileRegistry, Depends(get_profile_registry)]


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


@router.get("/{project_id}/private-chat")
async def get_private_chat(
    project_id: uuid.UUID, user_handle: str, db: DbSession
) -> dict:
    """Get-or-create the member's 1:1 private chat with 芝士 (spec §1)."""
    topic = await TopicService(db).get_or_create_private(
        project_id=project_id, user_handle=user_handle
    )
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


# --- ExecutionProfile (design §2): which model/provider this project runs on ---


@router.get("/{project_id}/execution-profiles")
async def list_execution_profiles(
    project_id: uuid.UUID, db: DbSession, registry: Registry
) -> dict:
    """Profiles this project may select (credentialed + permitted for its owner),
    plus the current selection. Default = our AI pool."""
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    current = (project.settings or {}).get("execution_profile") or "default"
    profiles = [asdict(v) for v in registry.selectable(project.owner_handle)]
    return ok({"current": current, "profiles": profiles})


@router.put("/{project_id}/execution-profile")
async def set_execution_profile(
    project_id: uuid.UUID, body: dict, db: DbSession, registry: Registry
) -> dict:
    """Set the project's execution profile. Only a profile that's selectable for
    this owner is accepted (a testing-tier profile on a non-dogfood project is
    rejected — review Finding 7)."""
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    name = (body.get("profile") or "").strip() or "default"
    allowed = {v.name for v in registry.selectable(project.owner_handle)}
    if name not in allowed:
        raise ValidationError(f"执行档案 {name!r} 对本项目不可用")
    project.settings = {**(project.settings or {}), "execution_profile": name}
    await db.flush()
    return ok({"current": name})


# --- Compute pool (design §3): which machine runs this project's sandbox ---


@router.get("/{project_id}/compute-profiles")
async def list_compute_profiles(project_id: uuid.UUID, db: DbSession) -> dict:
    """Compute pools this project may select (only the ones actually deployed),
    plus the current selection. Default = 知是本地算力."""
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    current = (project.settings or {}).get("compute_profile") or compute_default_name()
    profiles = [asdict(v) for v in compute_selectable(settings)]
    return ok({"current": current, "profiles": profiles})


@router.put("/{project_id}/compute-profile")
async def set_compute_profile(project_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """Set the project's compute pool. Only a deployed (available) pool is
    accepted, so a project never selects compute that isn't actually there."""
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    name = (body.get("profile") or "").strip() or compute_default_name()
    allowed = {v.id for v in compute_selectable(settings)}
    if name not in allowed:
        raise ValidationError(f"算力池 {name!r} 尚未接入，暂不可选")
    project.settings = {**(project.settings or {}), "compute_profile": name}
    await db.flush()
    return ok({"current": name})


# --- Environment (spec §9.1): which sandbox image runs this project's agent ---

# A docker image reference, e.g. "cheesex-dev:v0". Kept strict so the value can't
# smuggle anything into the sandbox shim's `docker run "$SBX_IMAGE"`.
_IMAGE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]*(:[a-zA-Z0-9._-]+)?$")

# Curated env images the UI offers. The default (None) = the pool's base image;
# cheesex-dev bakes this repo's toolchain for dogfooding on 知是 itself.
_SANDBOX_IMAGE_OPTIONS = [
    {"image": "cheesex-dev:v0", "label": "cheesex-dev（本仓库工具链 · dogfooding）"},
]


@router.get("/{project_id}/sandbox-image")
async def get_sandbox_image(project_id: uuid.UUID, db: DbSession) -> dict:
    """The project's env image: `current` (None = using the pool default),
    the `default` base image, and a few curated `options`."""
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    current = (project.settings or {}).get("sandbox_image")
    return ok(
        {
            "current": current,
            "default": settings.sandbox_image,
            "options": _SANDBOX_IMAGE_OPTIONS,
        }
    )


@router.put("/{project_id}/sandbox-image")
async def set_sandbox_image(project_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """Point a project at a specific env image (e.g. cheesex-dev:v0 for dogfooding),
    or clear it (empty → back to the pool default)."""
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    image = (body.get("image") or "").strip()
    new_settings = {**(project.settings or {})}
    if not image:
        new_settings.pop("sandbox_image", None)  # revert to the pool default
        current = None
    else:
        if not _IMAGE_RE.match(image):
            raise ValidationError(f"镜像名不合法：{image!r}")
        new_settings["sandbox_image"] = image
        current = image
    project.settings = new_settings
    await db.flush()
    return ok({"current": current})
