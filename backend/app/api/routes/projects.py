"""Project routes."""

import asyncio
import re
import uuid
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.deps import (
    get_chat_service,
    get_profile_registry,
    get_turn_runner,
    project_device_online,
)
from app.api.response import ok, page
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import NotFoundError, ValidationError
from app.domain.agent.chat import ChatService
from app.domain.agent.market import (
    compute_default_name,
    compute_selectable,
    subscription_model_default,
    subscription_model_ids,
    subscription_model_listings,
)
from app.domain.agent.profiles import ProfileRegistry
from app.domain.agent.roles import resolve_role_description
from app.domain.agent.runtime import TurnRunner
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.membership.repositories import MemberRepository
from app.domain.memory.models import MemoryScope
from app.domain.project.models import ProjectRole
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
from app.domain.topic_membership.services import TopicMemberService
from app.domain.workspace import service as ws
from app.domain.workspace import upstream_conflict

router = APIRouter(prefix="/api/projects", tags=["projects"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
Registry = Annotated[ProfileRegistry, Depends(get_profile_registry)]

QUALITY_GATE_COMMAND_MAX_CHARS = 4096


@router.post("")
async def create_project(
    body: ProjectCreate, db: DbSession, resolver: ActorResolverDep
) -> dict:
    # Whoever creates a project owns it unless they say otherwise. Without this
    # the listing — now scoped to the caller — would hide a project from the very
    # person who just made it.
    who = await resolver.resolve(fallback_handle=body.owner_handle)
    project = await ProjectService(db).create(
        name=body.name,
        owner_handle=body.owner_handle or (who.handle if who.handle else None),
        ai_mode=body.ai_mode,
        expert_role=body.expert_role,
        team_id=body.team_id,
        external_task_id=body.external_task_id,
    )
    return ok(ProjectOut.model_validate(project).model_dump(mode="json"))


@router.get("")
async def list_projects(
    db: DbSession, resolver: ActorResolverDep, team_id: int | None = None
) -> dict:
    """One team's 项目 page with ``team_id`` (a personal team also folds in its
    owner's legacy team-less projects); otherwise the caller's OWN projects.

    Without ``team_id`` this used to return every project to everyone. That is
    survivable while five exist and wrong as soon as a class does — a student
    would find every other team's work in their sidebar.
    """
    service = ProjectService(db)
    if team_id is not None:
        projects = await service.list_for_team(team_id)
        total = len(projects)
    else:
        who = await resolver.resolve(fallback_handle=None)
        if who.authenticated:
            projects = await ProjectRepository(db).list_visible_to(
                handle=who.handle, user_id=who.user_id
            )
            total = len(projects)
        else:
            # The unauthenticated surface is left exactly as it was. Every 2.0
            # route on this deployment is reachable without a credential
            # (handle-fallback, Phase 0), so making THIS one the exception would
            # not protect anything — a caller could simply not authenticate.
            # Tightening that surface is a decision about all of them, not a
            # side effect of scoping a sidebar. Real users are logged in, and
            # they are who this scoping is for.
            projects, total = await service.list_all()
    items = [ProjectOut.model_validate(p).model_dump(mode="json") for p in projects]
    return ok(page(items, total))


@router.get("/by-task/{task_id}")
async def projects_for_task(task_id: int, db: DbSession) -> dict:
    """The 2.0 projects created from this 赛题.

    The 赛题 page uses it to show what already exists rather than offering to
    create a second one blindly — a 赛题 with three teams on it should read as
    three projects, not as a button that quietly makes a fourth.
    """
    projects = await ProjectRepository(db).list_for_external_task(task_id)
    items = [ProjectOut.model_validate(p).model_dump(mode="json") for p in projects]
    return ok(page(items, len(items)))


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
async def unlink_task(project_id: uuid.UUID, task_id: uuid.UUID, db: DbSession) -> dict:
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


async def _agent_memory_scope(
    db: DbSession, project_id: uuid.UUID, topic_raw: str
) -> tuple[MemoryScope, str] | None:
    """Resolve ``topic`` into the acting 芝士's own memory scope in this project.

    What an agent learns is its own, the way a teammate's is — a project hosting
    several 芝士 must not pool one's operational trivia with another's product
    decisions. Returns ``None`` when no usable topic was supplied, so the caller
    falls back to the shared project pool.
    """
    from app.domain.memory.models import agent_project_scope_id

    try:
        topic_id = uuid.UUID(topic_raw)
    except ValueError:
        return None
    handle = await TopicMemberService(db).resolve_agent_handle(topic_id)
    return MemoryScope.agent_project, agent_project_scope_id(project_id, handle)


@router.post("/{project_id}/memory")
async def add_memory(project_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """记入记忆 — used by the `cheese remember` CLI. With a ``topic`` it writes
    the acting 芝士's own memory for this project; with scope="user"+owner it
    writes that member's personal memory (private chat, spec §8.4 个人记忆跟着
    人走). Without either it falls back to the shared project pool."""
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
        return ok({"remembered": True})
    agent_scope = await _agent_memory_scope(db, project_id, body.get("topic") or "")
    if agent_scope is not None:
        await memory_store(db).remember(*agent_scope, content)
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
    store = memory_store(db)
    if (body.get("scope") or "project") == "user":
        owner = (body.get("owner") or "").strip()
        if not owner:
            raise ValidationError("owner 不能为空（个人记忆需要 owner）")
        hits = await store.search(MemoryScope.user, owner, query)
        return ok({"hits": [h.as_dict() for h in hits]})
    # The agent's own memory first, then the shared pool — which is a read-only
    # tail of what was written before memory was split per agent.
    hits = []
    agent_scope = await _agent_memory_scope(db, project_id, body.get("topic") or "")
    if agent_scope is not None:
        hits.extend(await store.search(*agent_scope, query))
    hits.extend(await store.search(MemoryScope.project, str(project_id), query))
    return ok({"hits": [h.as_dict() for h in hits]})


@router.get("/{project_id}/private-chat")
async def get_private_chat(
    project_id: uuid.UUID,
    user_handle: str,
    db: DbSession,
    peer_handle: str | None = None,
) -> dict:
    """Get-or-create a 1:1 private chat (spec §1).

    Without ``peer_handle`` this is the member's 1:1 with 芝士. With
    ``peer_handle`` it is a person-to-person DM between the two humans, shared
    by both regardless of who opens it first.
    """
    topic = await TopicService(db).get_or_create_private(
        project_id=project_id, user_handle=user_handle, peer_handle=peer_handle
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
    device_online = await project_device_online(db, project_id)
    profiles = [
        asdict(v) for v in compute_selectable(settings, device_online=device_online)
    ]
    return ok({"current": current, "profiles": profiles})


@router.put("/{project_id}/compute-profile")
async def set_compute_profile(project_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """Set the project's compute pool. Only a deployed (available) pool is
    accepted, so a project never selects compute that isn't actually there."""
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    name = (body.get("profile") or "").strip() or compute_default_name()
    device_online = await project_device_online(db, project_id)
    allowed = {v.id for v in compute_selectable(settings, device_online=device_online)}
    if name not in allowed:
        raise ValidationError(f"算力池 {name!r} 尚未接入，暂不可选")
    project.settings = {**(project.settings or {}), "compute_profile": name}
    await db.flush()
    return ok({"current": name})


# --- Subscription model: which Claude model this project's subscription turns
# use (parallel to the compute pool). Only relevant when the subscription path is
# deployed; otherwise the listing is informational. -----------------------------


@router.get("/{project_id}/model-profiles")
async def list_model_profiles(project_id: uuid.UUID, db: DbSession) -> dict:
    """Claude models this project may select for subscription turns, plus the
    current selection. Default = Sonnet 5 (balanced / saves the subscription's
    quota)."""
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    current = (project.settings or {}).get(
        "subscription_model"
    ) or subscription_model_default()
    return ok(
        {
            "current": current,
            "profiles": [asdict(v) for v in subscription_model_listings()],
        }
    )


@router.put("/{project_id}/model-profile")
async def set_model_profile(project_id: uuid.UUID, body: dict, db: DbSession) -> dict:
    """Set the project's subscription model. Only a known model id is accepted."""
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    name = (body.get("profile") or "").strip() or subscription_model_default()
    if name not in subscription_model_ids():
        raise ValidationError(f"模型 {name!r} 不可选")
    project.settings = {**(project.settings or {}), "subscription_model": name}
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


async def require_quality_gate_admin(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> None:
    """Only a verified human project owner/lead may configure executable policy.

    A sandbox-scoped agent token is deliberately not accepted here: allowing an
    agent to choose the command that judges its own work is both a review bypass
    and, before gate isolation, a host-command primitive.
    """
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    if not actor.authenticated or actor.via != "token" or actor.is_agent:
        raise NotFoundError("Project not found")
    handle = actor.handle
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    if project.owner_handle == handle:
        return
    member = await MemberRepository(db).get(project_id=project_id, user_handle=handle)
    if member is not None and member.role == ProjectRole.lead:
        return
    # Conceal project existence from anonymous callers and outsiders.
    raise NotFoundError("Project not found")


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


@router.put(
    "/{project_id}/quality-gate",
    dependencies=[Depends(require_quality_gate_admin)],
)
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
        if "\x00" in command:
            raise ValidationError("check_command 不能包含 NUL 字节")
        if len(command) > QUALITY_GATE_COMMAND_MAX_CHARS:
            raise ValidationError(
                f"check_command 不能超过 {QUALITY_GATE_COMMAND_MAX_CHARS} 个字符"
            )
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
async def sync_project_upstream(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[TurnRunner, Depends(get_turn_runner)],
) -> dict:
    """同步上游: fetch + merge the upstream default branch into the project base.
    Conflicts abort cleanly and come back as {"synced": false, "reason": ...} —
    and, when we know who asked, 芝士 is dispatched at the materialized conflict
    so that report is a starting point instead of a dead end (spec §6.3, same
    contract as 采纳冲突 in routes/accept.py)."""
    await ProjectService(db).get_or_404(project_id)
    result = await asyncio.to_thread(ws.sync_upstream, project_id)
    if result.get("synced") or not result.get("conflicts"):
        return ok(result)
    # Anonymous callers get the old behaviour: with no handle there is no 1:1
    # room to put the work in, and inventing one would strand it.
    actor = await resolver.resolve(fallback_handle=None)
    if not actor.authenticated or not actor.handle:
        return ok(result)
    dispatched = await upstream_conflict.dispatch(
        db,
        project_id,
        requested_by=actor.handle,
        chat=chat,
        runner=runner,
    )
    if dispatched is not None:
        result = {**result, "dispatched": dispatched}
    return ok(result)
