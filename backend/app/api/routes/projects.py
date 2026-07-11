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
from app.domain.agent.roles import resolve_role_description
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
from app.domain.workspace import service as ws

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


@router.get("/by-team/{team_id}")
async def project_for_team(team_id: int, db: DbSession) -> dict:
    """The AI-workspace project for a 知是 Team (P4). ``data`` is null when the
    team has no project yet — the team page uses this to show/hide its 「AI 工作台」
    entry."""
    project = await ProjectRepository(db).get_by_team(team_id)
    data = (
        ProjectOut.model_validate(project).model_dump(mode="json")
        if project is not None
        else None
    )
    return ok(data)


@router.get("/{project_id}")
async def get_project(project_id: uuid.UUID, db: DbSession) -> dict:
    project = await ProjectService(db).get_or_404(project_id)
    return ok(ProjectOut.model_validate(project).model_dump(mode="json"))


@router.put("/{project_id}/expert-role")
async def set_expert_role(project_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """Set which expert persona 芝士 loads for this project (spec §8.2). Any
    known role name is accepted (custom shadows built-in); empty clears."""
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    name = str(body.get("role") or "").strip()
    if name and await resolve_role_description(db, name) is None:
        raise ValidationError(f"角色 {name!r} 不存在")
    project.expert_role = name or None
    await db.flush()
    return ok({"current": project.expert_role})


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
    from app.domain.memory.store import memory_store

    await ProjectService(db).get_or_404(project_id)
    content = (body.get("content") or "").strip()
    if not content:
        raise ValidationError("content 不能为空")
    if (body.get("scope") or "project") == "user":
        owner = (body.get("owner") or "").strip()
        if not owner:
            raise ValidationError("owner 不能为空（个人记忆需要 owner）")
        await memory_store(db).remember(MemoryScope.user, owner, content)
    else:
        await memory_store(db).remember(MemoryScope.project, str(project_id), content)
    return ok({"remembered": True})


@router.post("/{project_id}/memory/search")
async def search_memory(project_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """记忆检索 — used by the `cheese recall` CLI. Defaults to project memory;
    with scope="user"+owner it searches that member's personal memory. On the
    OpenViking backend this is semantic search returning L0 abstracts; the flat
    DB backend falls back to a substring filter."""
    from app.domain.memory.models import MemoryScope
    from app.domain.memory.store import memory_store

    await ProjectService(db).get_or_404(project_id)
    query = (body.get("query") or "").strip()
    if not query:
        raise ValidationError("query 不能为空")
    if (body.get("scope") or "project") == "user":
        owner = (body.get("owner") or "").strip()
        if not owner:
            raise ValidationError("owner 不能为空（个人记忆需要 owner）")
        scope, scope_id = MemoryScope.user, owner
    else:
        scope, scope_id = MemoryScope.project, str(project_id)
    hits = await memory_store(db).search(scope, scope_id, query)
    return ok({"hits": [h.as_dict() for h in hits]})


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


# --- Quality gate (spec §4.4/§9, eval C2): 平台硬门 configuration -------------


@router.get("/{project_id}/quality-gate")
async def get_quality_gate(project_id: uuid.UUID, db: DbSession) -> dict:
    """The project's 硬门 settings: `check_command` (run in the topic workspace
    before an accept card reaches the reviewer; empty = no gate) and
    `approvals_required` (distinct approvals an accept needs; default 1)."""
    from app.domain.review.services import approvals_required_of, check_command_of

    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return ok(
        {
            "check_command": check_command_of(project) or "",
            "approvals_required": approvals_required_of(project),
        }
    )


@router.put("/{project_id}/quality-gate")
async def set_quality_gate(project_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """Update 硬门 settings. Only the keys present in the body change; an empty
    check_command removes the gate."""
    from app.domain.review.services import approvals_required_of, check_command_of

    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    new_settings = {**(project.settings or {})}
    if "check_command" in body:
        command = str(body.get("check_command") or "").strip()
        if command:
            new_settings["check_command"] = command
        else:
            new_settings.pop("check_command", None)
    if "approvals_required" in body:
        try:
            required = int(body.get("approvals_required") or 0)
        except (TypeError, ValueError):
            raise ValidationError("approvals_required 必须是整数") from None
        if required < 1:
            raise ValidationError("approvals_required 至少为 1")
        new_settings["approvals_required"] = required
    project.settings = new_settings
    await db.flush()
    return ok(
        {
            "check_command": check_command_of(project) or "",
            "approvals_required": approvals_required_of(project),
        }
    )


@router.get("/{project_id}/upstream")
async def get_project_upstream(project_id: uuid.UUID, db: DbSession) -> dict:
    """The project's linked upstream repo (关联已有 repo, spec §6.3), if any."""
    await ProjectService(db).get_or_404(project_id)
    return ok({"url": ws.get_upstream(project_id)})


@router.put("/{project_id}/upstream")
async def set_project_upstream(
    project_id: uuid.UUID, body: dict, db: DbSession
) -> dict:
    """Link the project to an existing git repo (empty url → unlink). The repo's
    history then flows in via 同步上游, and stays syncable afterwards."""
    await ProjectService(db).get_or_404(project_id)
    url = ws.set_upstream(project_id, str(body.get("url") or ""))
    return ok({"url": url})


@router.post("/{project_id}/upstream/sync")
async def sync_project_upstream(project_id: uuid.UUID, db: DbSession) -> dict:
    """同步上游: fetch + merge the upstream default branch into the project base.
    Conflicts abort cleanly and come back as {"synced": false, "reason": ...}."""
    await ProjectService(db).get_or_404(project_id)
    return ok(ws.sync_upstream(project_id))
