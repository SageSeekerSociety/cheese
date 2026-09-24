"""Project routes."""

import asyncio
import logging
import re
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.deps import (
    get_chat_service,
    get_profile_registry,
    project_device_online,
)
from app.api.response import ok, page
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    SystemBusyError,
    ValidationError,
)
from app.domain.agent.chat import ChatService
from app.domain.agent.compute_configs import (
    ProjectComputeConfigs,
    project_configs,
    validate_choice,
)
from app.domain.agent.github_app import (
    github_app_read_token_for_project,
)
from app.domain.agent.market import (
    COMPUTE_CLOUD,
    COMPUTE_TIERS,
    compute_default_name,
    compute_selectable,
)
from app.domain.agent.profiles import ProfileRegistry
from app.domain.agent_instance.configuration import AgentConfiguration, model_choices
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_instance.schemas import (
    AgentInstanceCreate,
    AgentInstanceOut,
    AgentInstanceUpdate,
    ProjectDefaultAgentIn,
)
from app.domain.agent_instance.services import (
    AgentInstanceService,
    ResolvedAgent,
    memory_pool,
)
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.documents.text import delivered_comparison
from app.domain.identity.actor import Actor
from app.domain.identity.handles import ANONYMOUS_HANDLE, agent_instance_handle
from app.domain.library import service as library
from app.domain.machine.limits import get_machine_limit
from app.domain.machine.services import MachineService
from app.domain.membership.repositories import MemberRepository
from app.domain.membership.services import MemberService
from app.domain.memory.models import MemoryScope
from app.domain.policy import gate
from app.domain.preview.office import (
    OfficeRenderFailed,
    OfficeRenderUnavailable,
    is_renderable,
    render_to_pdf,
)
from app.domain.project import artifacts
from app.domain.project.models import Project, ProjectRole
from app.domain.project.protection import (
    BRANCH_PROTECTION_KEY,
    GITHUB_UNBOUND,
    BranchProtection,
    apply_branch_protection_update,
    branch_protection_of,
    github_repo_snapshot,
)
from app.domain.project.repositories import (
    ProjectGitInstallationRepository,
    ProjectRepository,
)
from app.domain.project.schemas import (
    ForgeAttributionUpdate,
    ProjectCreate,
    ProjectDefaultModelUpdate,
    ProjectOut,
)
from app.domain.project.services import ProjectService
from app.domain.repository.forge_files import ProjectFiles
from app.domain.review.repositories import AcceptCardRepository
from app.domain.room_task import presentation
from app.domain.room_task.place import Place
from app.domain.room_task.repositories import TaskRepository
from app.domain.room_task.schemas import TaskOut
from app.domain.shell.catalog import Shell
from app.domain.shell.schemas import ShellOut
from app.domain.shell.service import effective_shells
from app.domain.team.services import team_service
from app.domain.topic.schemas import TopicOut
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

logger = logging.getLogger("cheesex.projects")


router = APIRouter(prefix="/projects", tags=["projects"])
DbSession = Annotated[AsyncSession, Depends(get_db)]
Registry = Annotated[ProfileRegistry, Depends(get_profile_registry)]


def _shelled(project: Project, shell: Shell, team_handle: str | None) -> dict:
    """One project's wire payload, with the 壳 it runs under and its team's handle.

    Neither is a column on `Project`, so neither can come from `model_validate`:
    the 壳 is resolved from the project's own settings, its 赛题 and that 赛题's
    项目集, and the handle is what links to the team go by. Every route that
    returns a project goes through `_project_payloads`, or the frontend would
    fall back to the default 壳 — or lose the way to the team — on some screens
    and not others.
    """
    return (
        ProjectOut.model_validate(project)
        .model_copy(update={"shell": ShellOut.of(shell), "team_handle": team_handle})
        .model_dump(mode="json")
    )


async def _project_payloads(db: DbSession, projects: list[Project]) -> list[dict]:
    """Every project's payload, without a query per project."""
    shells = await effective_shells(db, projects)
    handles = await team_service(db).handles_of(
        {p.team_id for p in projects if p.team_id is not None}
    )
    return [
        _shelled(p, shells[p.id], handles.get(p.team_id) if p.team_id else None)
        for p in projects
    ]


async def _project_payload(db: DbSession, project: Project) -> dict:
    return (await _project_payloads(db, [project]))[0]


@router.get("/resource-limits")
async def resource_limits(db: DbSession) -> dict:
    """Creation defaults, available before a project exists."""
    return ok(
        {
            "max_machines_per_team": await get_machine_limit(db),
            "max_concurrent_turns": settings.max_concurrent_turns,
        }
    )


@router.post("")
async def create_project(
    body: ProjectCreate, db: DbSession, resolver: ActorResolverDep
) -> dict:
    # Whoever creates a project owns it unless they say otherwise. Without this
    # the listing — now scoped to the caller — would hide a project from the very
    # person who just made it.
    who = await resolver.resolve(fallback_handle=body.owner_handle)
    # An unidentified caller resolves to the literal `anonymous` (auth.py), and
    # storing that as the owner is worse than storing nothing: it reads like a
    # person everywhere downstream, and it blocks the ownerless-room escape
    # hatch, which opens only on an ABSENT owner and deliberately refuses to
    # judge a handle by its name. NULL is the honest value for "we do not know".
    owner_handle = body.owner_handle or (
        who.handle if who.authenticated and who.handle else None
    )
    if not owner_handle or owner_handle == ANONYMOUS_HANDLE:
        # Silence is how this got expensive (#315). A project whose owner is not
        # a real person can be repaired — PUT /{id}/owner exists now — but
        # nothing else in the system will ever mention it: all seven readers of
        # the field fall back to `lead` without erroring, so the gap surfaces
        # only as "why can only one person do anything here", six days later.
        #
        # The check covers `anonymous` as well as empty, because the empty case
        # is no longer the one that happens. `resolve()` hands back the literal
        # handle `anonymous` rather than nothing, so an unidentified creator now
        # produces a *populated* owner column that still matches no user — the
        # same collapse onto `lead`, wearing a value.
        #
        # It does NOT ask whether the owner is a person. An agent instance is a
        # participant and holds a project role like anybody else, so a handle
        # that names one is an owner this log has nothing to warn about; reading
        # the handle's SHAPE to decide otherwise was the platform guessing at a
        # participant's kind from its name.
        logger.warning(
            "project created without a real owner name=%r owner=%r",
            body.name,
            owner_handle,
        )
    project = await ProjectService(db).create(
        name=body.name,
        owner_handle=owner_handle,
        ai_mode=body.ai_mode,
        agent_type=body.agent_type,
        agent_name=body.agent_name,
        team_id=body.team_id,
        external_task_id=body.external_task_id,
        forge_kind=body.forge_kind,
    )
    # The caller can create a room as soon as this response arrives; the
    # request-scoped dependency commits only after sending the response.
    await db.commit()
    return ok(await _project_payload(db, project))


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
        # A team's project list is not a directory: every row carries the
        # project's `id`, and that id opens its roster, decisions and usage. So
        # this answered "which projects does that team have, and what are their
        # ids" to anyone who asked — including callers with no credential at
        # all, while the SAME route without `team_id` was strict.
        await resolver.authorize_team(
            await resolver.resolve(fallback_handle=None), team_id=team_id
        )
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
            # 认不出人 ≠ 认识所有人。This used to return `service.list_all()`,
            # on the argument that every 2.0 route is reachable without a
            # credential anyway, so tightening one of them protects nothing.
            # That argument is wrong here, and measurably so: the failure mode
            # is not "an anonymous stranger browses", it is "a LOGGED-IN user's
            # token lapsed". Measured on dev 2026-08-12 — same browser, same
            # second: a valid token returns 1 project, `Bearer not.a.jwt`
            # returns 12, including four other people's. The 2.0 access token
            # lives about three minutes and this fetch layer has no refresh
            # (it is raw `fetch`, so the axios 401 interceptor never sees it),
            # so every user crosses that boundary constantly — which is exactly
            # what「有时候左边栏冒出一堆不是我的项目」was.
            #
            # Whatever else is open, THIS route's meaning without `team_id` is
            # "the caller's OWN projects" — with no caller, the honest answer is
            # none, not all.
            #
            # But "none" is only honest for a caller who presented nothing. The
            # user this bug was actually about DID present a token; it just
            # failed. Answering them 200-with-nothing swaps one undetectable
            # wrong answer for another — measured on dev 2026-08-12: a bearer
            # with a bad signature got `200 n=0` here while the same token got
            # 401 from `topic-unread`. So the sidebar sat empty until some other
            # route happened to 401 and trip the refresh. Say it here instead.
            resolver.reject_failed_credential(who)
            projects, total = [], 0
    items = await _project_payloads(db, projects)
    return ok(page(items, total))


@router.get("/by-task/{task_id}")
async def projects_for_task(
    task_id: int, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The 2.0 projects created from this 赛题.

    The 赛题 page uses it to show what already exists rather than offering to
    create a second one blindly — a 赛题 with three teams on it should read as
    three projects, not as a button that quietly makes a fourth.

    Who may ask is decided by the task, not by the project: every row carries a
    project id, a name and an owner handle, and the id is the key to the rest of
    these routes. So the door is ``authorize_task`` — the task's own visibility
    judgment — and the 出题者 (``Task.creator_id``) is the caller this exists
    for.
    """
    actor = await resolver.resolve(fallback_handle=None)
    await resolver.authorize_task(actor, task_id=task_id)
    projects = await ProjectRepository(db).list_for_external_task(task_id)
    items = await _project_payloads(db, projects)
    return ok(page(items, len(items)))


@router.get("/by-team/{team_id}")
async def project_for_team(
    team_id: int, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The AI-workspace project for a 知是 Team (P4). ``data`` is null when the
    team has no project yet — the team page uses this to show/hide its 「AI 工作台」
    entry.

    ``/projects?team_id=`` next door has always answered only to a member of the
    team (``authorize_team``); this route hands out the same project by the same
    guessable id and had no door at all, so the parameter was the guarded way in
    and the path was the way around it.
    """
    actor = await resolver.resolve(fallback_handle=None)
    await resolver.authorize_team(actor, team_id=team_id)
    project = await ProjectRepository(db).get_by_team(team_id)
    data = await _project_payload(db, project) if project is not None else None
    return ok(data)


@router.get("/{project_id}")
async def get_project(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    return ok(await _project_payload(db, project))


def _holds_the_default(project: Project, row: AgentInstance) -> bool:
    """Whether this row is what a new topic in the project gets.

    One way to be it: the project points at it. A project is created with its
    芝士 and pointed at it right there, so there is no longer a second way — an
    agent that holds the default before anything points at it.
    """
    return row.id == project.default_agent_instance_id


def _agent_out(
    project_id: uuid.UUID,
    agent: ResolvedAgent,
    *,
    is_default: bool,
    is_active: bool = True,
) -> dict:
    return AgentInstanceOut(
        id=agent.instance_id,
        project_id=project_id,
        handle=agent.handle,
        seat_handle=agent_instance_handle(agent.instance_id),
        type_name=agent.type_name,
        display_name=agent.display_name,
        configuration=AgentConfiguration.model_validate(agent.configuration),
        is_default=is_default,
        is_active=is_active,
    ).model_dump(mode="json")


@router.get("/{project_id}/agents")
async def list_project_agents(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The project's saved agents, including its default for new rooms."""
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    service = AgentInstanceService(db)
    await service.for_project(project)
    rows = await service.list_for_project(project_id)
    items = [
        _agent_out(
            project_id,
            AgentInstanceService.resolved(row),
            is_default=_holds_the_default(project, row),
            is_active=row.is_active,
        )
        for row in rows
    ]
    return ok(page(items, len(items)))


@router.post("/{project_id}/agents")
async def create_project_agent(
    project_id: uuid.UUID,
    body: AgentInstanceCreate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Add an agent to this project. It starts with an empty memory pool."""
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await ProjectService(db).get_or_404(project_id)
    service = AgentInstanceService(db)
    instance = await service.create(
        project_id=project_id,
        handle=body.handle or f"agent-{uuid.uuid4().hex[:8]}",
        type_name=body.type_name,
        display_name=body.display_name,
        configuration=body.configuration,
    )
    await db.flush()
    return ok(
        _agent_out(
            project_id, AgentInstanceService.resolved(instance), is_default=False
        )
    )


@router.put("/{project_id}/agents/{agent_id}")
async def update_project_agent(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    body: AgentInstanceUpdate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Edit one agent's name and saved configuration.

    ``handle`` is not editable and is not accepted here: it keys the memory
    pool, so changing it would hand the agent an empty one and orphan
    everything it had learned in this project.
    """
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    service = AgentInstanceService(db)
    instance = await service.get_in_project(project_id=project_id, instance_id=agent_id)
    fields = body.model_fields_set
    if "display_name" in fields and body.display_name is not None:
        await service.rename(instance, body.display_name)
    if body.configuration is not None:
        await service.configure(instance, body.configuration)
    await db.flush()
    return ok(
        _agent_out(
            project_id,
            AgentInstanceService.resolved(instance),
            is_default=_holds_the_default(project, instance),
            is_active=instance.is_active,
        )
    )


@router.delete("/{project_id}/agents/{agent_id}")
async def deactivate_project_agent(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Retire an agent — not a delete.

    The rooms already working with it carry on and its memory is kept; it just
    stops being offered for new work. The response says ``deleted`` because
    that is the shape a DELETE returns everywhere here, not because a row went
    away.
    """
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    service = AgentInstanceService(db)
    instance = await service.get_in_project(project_id=project_id, instance_id=agent_id)
    await service.deactivate(project, instance)
    return ok({"deleted": True})


@router.put("/{project_id}/default-agent")
async def set_project_default_agent(
    project_id: uuid.UUID,
    body: ProjectDefaultAgentIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Select the existing agent that new rooms start with."""
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    service = AgentInstanceService(db)
    instance = await service.get_in_project(
        project_id=project_id, instance_id=body.instance_id
    )
    agent = await service.set_project_default(project, instance)
    return ok(_agent_out(project_id, agent, is_default=True))


@router.get("/{project_id}/library/raw")
async def library_file_raw(
    project_id: uuid.UUID,
    path: str,
    db: DbSession,
    resolver: ActorResolverDep,
    topic: str = "",
) -> Response:
    """一份资料的字节。给下载，也给 `cheese library get`——芝士 要读一份没有被这条
    消息带上的资料时，只能自己来取（那时带着它干活的那个话题，见 `_authorized_place`）。
    """
    await ProjectService(db).get_or_404(project_id)
    await _project_reader(db, resolver, project_id, topic)
    name = _library_path(path)
    data = library.read_library_file(project_id, name)
    filename = quote(name.rsplit("/", 1)[-1], safe="")
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, max-age=3600",
        },
    )


def _library_path(raw: str) -> str:
    """资料库里那一份的名字——它就是地址，所以这里只挡不是名字的东西。"""
    name = (raw or "").strip()
    if not name or name.startswith("/") or ".." in name.split("/"):
        raise ValidationError("path 必须是资料库里的相对路径")
    return name


async def _project_reader(
    db: DbSession,
    resolver: ActorResolverDep,
    project_id: uuid.UUID,
    topic_raw: str,
) -> None:
    """谁读得到这个项目的东西：项目成员，或者正在这个项目某个话题里干活的 芝士。"""
    if topic_raw:
        await _authorized_place(db, resolver, project_id, topic_raw)
        return
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)


@router.get("/{project_id}/library")
async def list_library(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep, topic: str = ""
) -> dict:
    """资料库：用户给这个项目的文件，按原名，每个房间都引用得到。

    Project-level on purpose — 「上周那份预算表」is a sentence someone says in a
    room that has never seen that file."""
    await ProjectService(db).get_or_404(project_id)
    await _project_reader(db, resolver, project_id, topic)
    files = library.list_library_files(project_id)
    return ok(page(files, len(files)))


@router.get("/{project_id}/artifacts")
async def list_artifacts(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep, topic: str = ""
) -> dict:
    """产物清单：这个项目交出去的东西，一项一行 (#1085 结论二、三)。

    清单只读，而且没有配套的新建入口：它由交付长出来 —— 交出去一次合并的，落在项目
    那个仓库那一项上（平台自己认）；交出去一份文件或一个地址的，递卡时点名的名字不
    在清单上就当场多一项。所以这里没有 POST，不是还没做。"""
    await ProjectService(db).get_or_404(project_id)
    await _project_reader(db, resolver, project_id, topic)
    rows = await artifacts.list_for_project(db, project_id)
    items = [
        {
            "id": str(a.id),
            "name": a.name,
            "about": a.about,
            "version": a.version,
            "delivered_at": a.delivered_at.isoformat() if a.delivered_at else None,
        }
        for a in rows
    ]
    return ok(page(items, len(items)))


@router.get("/{project_id}/artifacts/{artifact_id}")
async def read_artifact(
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    topic: str = "",
) -> dict:
    """清单上这一项自己的那一页 (#1085 结论二)：现在是第几版，以及交付过的每一版。

    一版就是一张采纳了的卡，所以这里没有「版本表」——历史是数出来的，撤回一次采
    纳，它后面几版的号自己往前挪。"""
    await ProjectService(db).get_or_404(project_id)
    await _project_reader(db, resolver, project_id, topic)
    row = await artifacts.get_or_404(db, project_id=project_id, artifact_id=artifact_id)
    listed = await artifacts.summary(db, row.id)
    history = await artifacts.versions(db, row.id)
    return ok(
        {
            "id": str(row.id),
            "name": row.name,
            "about": row.about,
            "version": listed.version if listed else 0,
            "delivered_at": (
                listed.delivered_at.isoformat()
                if listed and listed.delivered_at
                else None
            ),
            "versions": [
                {
                    "number": v.number,
                    "card_id": str(v.card_id),
                    "subject": v.subject,
                    "delivered_at": (
                        v.delivered_at.isoformat() if v.delivered_at else None
                    ),
                    "decided_by": v.decided_by,
                    "kind": v.kind,
                    "filename": v.filename,
                    "url": v.url,
                }
                for v in history
            ],
        }
    )


@router.get("/{project_id}/artifacts/{artifact_id}/compare")
async def compare_artifact_versions(
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    before: uuid.UUID,
    after: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    topic: str = "",
) -> dict:
    await ProjectService(db).get_or_404(project_id)
    await _project_reader(db, resolver, project_id, topic)
    await artifacts.get_or_404(db, project_id=project_id, artifact_id=artifact_id)
    history = {v.card_id: v for v in await artifacts.versions(db, artifact_id)}
    if before not in history or after not in history:
        raise NotFoundError("这一项没有所选的交付版本")
    left, right = history[before], history[after]
    result = {
        "kind": "unavailable",
        "identical": None,
        "files": [],
        "note": "unavailable",
    }
    if left.kind == right.kind == "file" and left.filename and right.filename:
        old = library.read_artifact_snapshot(project_id, before, left.filename)
        new = library.read_artifact_snapshot(project_id, after, right.filename)
        comparison = await asyncio.to_thread(
            delivered_comparison, old, new, left.filename, right.filename
        )
        result = {
            "kind": "file",
            "identical": comparison["identical"],
            "files": [{"path": right.filename, **comparison}],
            "note": None,
        }
    elif left.kind == right.kind == "merge" and left.revision and right.revision:
        changes = await ProjectFiles(db, project_id, None).compare_revisions(
            left.revision, right.revision
        )
        result = {
            "kind": "merge",
            "identical": not changes,
            "files": changes,
            "note": "source",
        }
    elif left.kind == right.kind == "link":
        result = {
            "kind": "link",
            "identical": left.url == right.url,
            "files": [],
            "note": "link",
        }
    return ok(result)


@router.get("/{project_id}/artifacts/{artifact_id}/versions/{card_id}/file")
async def download_artifact_version(
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    card_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    topic: str = "",
    preview_pdf: bool = False,
) -> Response:
    """这一版交出去的那一份字节 (#1085 结论五)。

    取的是当时交出去的那个快照，不是现在从源重建一次的结果：半年之后依赖变了、字
    体没了，重建出来的可能和当时交出去的不是同一份东西，而用户要的是他交出去的那
    一份。"""
    await ProjectService(db).get_or_404(project_id)
    await _project_reader(db, resolver, project_id, topic)
    await artifacts.get_or_404(db, project_id=project_id, artifact_id=artifact_id)
    version = next(
        (v for v in await artifacts.versions(db, artifact_id) if v.card_id == card_id),
        None,
    )
    if version is None:
        raise NotFoundError("这一项没有这一版")
    if version.kind != "file" or not version.filename:
        # 交出去的是一个地址、或者一次合并：没有可下载的文件，而这不是缺东西。
        raise NotFoundError("这一版交出去的不是一份文件")
    data = library.read_artifact_snapshot(project_id, card_id, version.filename)
    if preview_pdf:
        if len(data) > 10 * 1024 * 1024:
            raise ValidationError("文件超过 10 MB，无法生成预览")
        if not is_renderable(version.filename):
            raise ValidationError("这个格式不能转换为预览")
        try:
            data = await render_to_pdf(
                data, version.filename, settings.office_render_endpoint
            )
        except OfficeRenderUnavailable as exc:
            raise SystemBusyError(str(exc)) from exc
        except OfficeRenderFailed as exc:
            raise ValidationError(str(exc)) from exc
    filename = quote(version.filename, safe="")
    return Response(
        content=data,
        media_type="application/pdf" if preview_pdf else "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, max-age=3600",
        },
    )


async def _artifact_keeper(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> None:
    """改清单的只有人。

    一轮里铸出来的凭据过不了 `authorize_project`，所以 芝士 改不了、合不了、删不
    了清单上的东西 —— 它只能在交付时声明，而「这两项是不是同一个东西」「这个名字
    对不对」正是要人判断的那部分。"""
    await ProjectService(db).get_or_404(project_id)
    actor = await resolver.require_verified_caller(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)


@router.patch("/{project_id}/artifacts/{artifact_id}")
async def rename_artifact(
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """给清单上这一项换个名字。

    卡指着的是这一行的 id，所以改名之后，之前的每一次交付照样算这一项的版本 ——
    名字起错了的正解是改名，不是删掉重来。"""
    await _artifact_keeper(project_id, db, resolver)
    row = await artifacts.get_or_404(db, project_id=project_id, artifact_id=artifact_id)
    renamed = await artifacts.rename(db, row, name=str(body.get("name") or ""))
    await db.commit()
    return ok({"id": str(renamed.id), "name": renamed.name})


@router.post("/{project_id}/artifacts/{artifact_id}/merge")
async def merge_artifact(
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """这两项其实是同一个东西：把这一项的交付都算到 `into` 那一项上。

    留哪个名字是人的判断，所以方向由调用方给，平台不挑。"""
    await _artifact_keeper(project_id, db, resolver)
    source = await artifacts.get_or_404(
        db, project_id=project_id, artifact_id=artifact_id
    )
    target = await artifacts.get_or_404(
        db, project_id=project_id, artifact_id=_artifact_ref(body.get("into"))
    )
    kept = await artifacts.merge(db, source=source, target=target)
    await db.commit()
    return ok({"id": str(kept.id), "name": kept.name})


@router.delete("/{project_id}/artifacts/{artifact_id}")
async def delete_artifact(
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """把这一项从清单上去掉 —— 用户说它本来就不该是一项。

    声明过它的那些卡留在原处，只是不再指向任何一项：那些交付确实发生过。"""
    await _artifact_keeper(project_id, db, resolver)
    row = await artifacts.get_or_404(db, project_id=project_id, artifact_id=artifact_id)
    await artifacts.delete(db, row)
    await db.commit()
    return ok({"deleted": True})


def _artifact_ref(raw: object) -> uuid.UUID:
    """合并的目标 —— 清单上另一项的 id。"""
    try:
        return uuid.UUID(str(raw or ""))
    except ValueError as exc:
        raise ValidationError("into 必须是清单上另一项的 id") from exc


@router.delete("/{project_id}/library")
async def delete_library_file(
    project_id: uuid.UUID, path: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """扔掉一份资料。

    这条路不收 `topic`：读资料库的是人和 芝士，扔掉它的只有人。一轮里铸出来的凭据
    过不了 `authorize_project`，所以 芝士 连同它自己正在读的那一份都删不掉。"""
    await ProjectService(db).get_or_404(project_id)
    actor = await resolver.require_verified_caller(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    library.delete_library_file(project_id, _library_path(path))
    return ok({"deleted": True})


@router.get("/{project_id}/decisions")
async def list_decisions(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """决策记录 (spec §7.1): project-wide decision blocks, each traceable to its
    source topic via topic_id.

    These are the project's own words, not metadata about it — the same content
    ``/topics`` has always guarded."""
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await ProjectService(db).get_or_404(project_id)
    blocks = await BlockRepository(db).list_by_kind_for_project(
        project_id, BlockKind.decision
    )
    items = [BlockOut.model_validate(b).model_dump(mode="json") for b in blocks]
    return ok(page(items, len(items)))


@router.get("/{project_id}/weeklies")
async def list_weeklies(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """周报集 (spec §7.1): project-wide weekly blocks, newest first.

    Each carries the stretch it covers in `meta` (`since`/`until`). A weekly
    report says what happened over a piece of time rather than what the project
    looks like right now, so that window is what tells two of them apart.

    Same shape as /decisions and for the same reason: these are the project's
    own words, and each is traceable to the room it was written in via
    `topic_id`."""
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await ProjectService(db).get_or_404(project_id)
    blocks = await BlockRepository(db).list_by_kind_for_project(
        project_id, BlockKind.weekly
    )
    items = [BlockOut.model_validate(b).model_dump(mode="json") for b in blocks]
    return ok(page(items, len(items)))


@router.get("/{project_id}/tasks")
async def list_project_tasks(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """Every thread in the project, each with the card it currently rides on.

    The rail draws rooms and the work inside them, so it needs both halves at
    once. Two round trips, not two per room and one per thread: a project here
    already holds ~170 rooms, and the per-room shape (`/topics/{id}/tasks`)
    would make painting one sidebar 170 requests before a single PR badge.

    `card` is the newest accept card ON THAT THREAD, narrowed to what a rail row
    can show — where the work stands and the PR it rides on. Null for a thread
    that has not been filed for acceptance, which is most of them while the work
    is still going.

    `presentation` is the board's answer for that row — which column it is in
    and the one phrase to print on it — derived here rather than in the client,
    so every client gives the same answer (`room_task/presentation.py`). Two
    round trips still: it is computed from the two batches already fetched.
    """
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await ProjectService(db).get_or_404(project_id)
    tasks = await TaskRepository(db).list_for_project(project_id)
    task_ids = [t.id for t in tasks]
    cards = await AcceptCardRepository(db).latest_by_task(task_ids)
    # 每条活最后一次说话是什么时候 —— 看板判「失联」的心跳。第三次批查询，走的是
    # blocks 上那条 (task_id, created_at) 的部分索引，不是每条活一次。
    beats = await TaskRepository(db).last_block_at_for_tasks(task_ids)
    # 哪几条停在一个未回答的提问上 —— 第四次批查询，同一条 (task_id, created_at)
    # 索引。这是唯一会中断「运行中」的一格，所以不能留给调用方各自去问。
    asked = await BlockRepository(db).tasks_awaiting_an_answer(task_ids)
    # 一次，给全部行用同一个「现在几点」：逐行取 now 会让同一批数据里两条本该
    # 一样的活分到不同格子，而那种差别没人再能复现。
    now = datetime.now(UTC)
    # 分身住在它房间的会话里，所以这一位按房间问，一个房间只问一次（内存里的
    # 当下事实，不走库）。
    live_rooms = {t.room_id: chat.has_live_screen(t.room_id) for t in tasks}
    items = []
    for task in tasks:
        card = cards.get(task.id)
        shown = presentation.task_presentation(
            presentation.facts_for_task(
                task,
                card,
                beats.get(task.id),
                room_screen_live=live_rooms[task.room_id],
                worker_live=chat.worker_live(task.room_id, task.subagent_id),
                awaiting_answer=task.id in asked,
            ),
            now=now,
        )
        items.append(
            {
                **TaskOut.model_validate(task).model_dump(mode="json"),
                "presentation": shown.as_dict(),
                "card": None
                if card is None
                else {
                    "id": str(card.id),
                    "status": str(card.status),
                    "pr_number": card.pr_number,
                    "pr_url": card.pr_url,
                },
            }
        )
    return ok(page(items, len(items)))


async def _authorized_place(
    db: DbSession,
    resolver: ActorResolverDep,
    project_id: uuid.UUID,
    topic_raw: str,
) -> tuple[Place, Actor] | None:
    """Resolve and authorize the caller-named place, when present, and say
    who is calling — the pool a memory goes to is that caller's.

    A place, not a room: `cheese remember` is run by whoever is doing the work,
    and that is usually a thread. Resolving only rooms answered 404 for the one
    caller this endpoint exists for.

    This is also how 芝士 reaches a project-level route at all: its credential
    is minted for one turn in one place, so a bare `authorize_project` refuses
    it (403) even though the token is valid and the project is right.
    """
    if not topic_raw:
        return None
    try:
        topic_id = uuid.UUID(topic_raw)
    except ValueError as exc:
        raise ValidationError("topic 不是合法的话题 id") from exc
    place = await TopicService(db).place_or_404(topic_id)
    if place.project_id != project_id:
        raise ForbiddenError("这个话题不属于 URL 中的项目")
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=place.room_id, project_id=project_id
    )
    await resolver.authorize_topic(actor, project_id=project_id, topic_id=place.room_id)
    return place, actor


async def _calling_agent(
    db: DbSession, project_id: uuid.UUID, caller: tuple[Place, Actor] | None
) -> ResolvedAgent | None:
    """Whose memory this call writes to and reads from.

    The AGENT, not the room: a room seats any number of teammates, and the one
    running `cheese remember` is the one on the token, so its notes go to its
    own pool wherever it is working — the same 芝士 moving between rooms keeps
    one pool. A token that names no saved teammate (an older one, a DM's)
    writes as the place's default: the DM's own teammate, else the project's.
    Returns ``None`` when no usable place was supplied; the caller then names
    the project's own 芝士 (`_agent_speaking`), because a memory always belongs
    to one instance.

    The agent itself rather than a pool key, because two different pools are
    named from it now: its own (`memory_pool`) and one per person it has
    formed a view of (`user_scope_id`).
    """
    if caller is None:
        return None
    place, actor = caller
    project = await ProjectService(db).get_or_404(project_id)
    agents = AgentInstanceService(db)
    # The seat handle answers for itself: `for_seat_handle` matches it against
    # the project's saved teammates and returns None for a person, the shared
    # `cheese` seat and a room-derived one. Pre-filtering by "is the caller an
    # agent" asked a second, coarser question whose only effect was to skip a
    # lookup that already says no.
    agent = await agents.for_seat_handle(project, actor.handle)
    if agent is None:
        agent = await agents.for_topic(place.room, project)
    return agent


async def _agent_speaking(
    db: DbSession, project: Project, agent: ResolvedAgent | None
) -> ResolvedAgent:
    """Which 芝士 this call is — the caller's, else the project's own.

    Every memory names an instance: a pool about a person (结论 8) and the
    project pool an instance keeps for itself (结论 7 — there is no pool the
    project shares). An endpoint called without a place still has to name one,
    and every project has its own 芝士 (结论 4), so a project-level call is
    that one speaking.

    Takes the caller's agent rather than resolving it again: every endpoint
    that asks this already had to resolve it for something else on the same
    path.
    """
    if agent is not None:
        return agent
    return await AgentInstanceService(db).for_project(project)


async def _authorize_personal_memory_owner(
    db: DbSession, place: Place | None, owner: str
) -> None:
    """个人记忆 lives in a private chat, and a private chat is a room — so this
    asks the room even when a thread inside it is the caller.

    Who is in that room is the roster's answer: a private chat is a room with
    two seats (结论 19), and those two are its participants."""
    if place is None:
        return
    room = place.room
    seats = await TopicMemberService(db).private_seats(room.id)
    if not room.is_private or seats is None or owner not in seats:
        raise ForbiddenError("只能在该成员自己的私聊中读写个人记忆")


#: 芝士 写给所有人看的那几条，在总览文档里自己占一节。
#:
#: 裸追加到文档末尾的那一份读起来是另一回事：它落在最后一个标题底下，种子文档
#: 那份的最后一节叫「## 数据」，读的人就把它当成数据那一节的内容。一个固定的标
#: 题把出处说清楚——这几行是芝士 观察到的、写给大家看的。
FACTS_FOR_EVERYONE = "## 大家都该知道的"


def _with_fact_for_everyone(current: str, content: str, handle: str) -> str:
    """把一条事实添进总览文档「大家都该知道的」那一节，署写它的那位的名。

    署名写在正文里，不是只写在 `Block.author` 上：那一栏只记最近一次编辑的人，
    下一个改文档的人一盖，这条事实就成了没有出处的一句话——而文档是给人看的，
    看的人要知道这句话是谁说的、找谁问。

    同一节里往下添，不是每条另起一个标题：一份被一条条观察切碎的文档，人不会再
    往里写字。
    """
    body = current.rstrip()
    line = f"- {content} —— @{handle}"
    if not body:
        return f"{FACTS_FOR_EVERYONE}\n\n{line}"
    lines = body.split("\n")
    start = next(
        (i for i, text in enumerate(lines) if text.strip() == FACTS_FOR_EVERYONE),
        None,
    )
    if start is None:
        return f"{body}\n\n{FACTS_FOR_EVERYONE}\n\n{line}"
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(r"#{1,6} ", lines[i])),
        len(lines),
    )
    # 这一节和下一个标题之间的空行留在原处：添在它前面，不是后面。
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    lines.insert(end, line)
    return "\n".join(lines)


@router.post("/{project_id}/memory")
async def add_memory(
    project_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """记入记忆 — used by the `cheese remember` CLI. With a ``topic`` it writes
    the acting 芝士's own memory for this project; with scope="user"+owner it
    writes that agent's view of that person, inside this project (结论 8).
    Called without a place, it is the project's own 芝士 writing.

    ``scope="everyone"`` is not a memory at all: 人和 agent 共同看的东西只能是
    文档（结论 7），so a fact everyone must know is appended to the project
    overview room's living doc, where it is signed, versioned, editable by the
    people it is for, and read by every room. 项目没有共享记忆池，那一路的去向
    就是这一份文档。

    Before a *memory* is stored, the fact is looked for in the live checkout: a
    memory is for what the repo cannot tell you (结论 61), and a fact that is
    already written in a file there is a copy that will go stale on its own.
    The refusal names the file, because "已经写在 repo 里了" without it leaves
    the caller nothing to do but rephrase and try again. 文档那一路不问这一
    句——它写的本来就是给人看的那一份。

    ``layer="core"`` buys a seat in every future prompt instead of a place in
    the pool that gets retrieved on demand — see MemoryLayer."""
    from app.domain.agent.harness import harness_for
    from app.domain.memory.models import MemoryLayer, user_scope_id
    from app.domain.memory.redundant import agent_checkout_search, already_in_repo
    from app.domain.memory.store import memory_store

    project = await ProjectService(db).get_or_404(project_id)
    caller = await _authorized_place(
        db, resolver, project_id, (body.get("topic") or "").strip()
    )
    place = caller[0] if caller else None
    content = (body.get("content") or "").strip()
    if not content:
        raise ValidationError("content 不能为空")
    raw_layer = (body.get("layer") or MemoryLayer.fact.value).strip()
    if raw_layer not in tuple(MemoryLayer):
        raise ValidationError("layer 只能是 core 或 fact")
    layer = MemoryLayer(raw_layer)
    scope = (body.get("scope") or "project").strip()
    # 谁在调用，这一句就答完了：下面三处都用它——查哪条检出目录（手是这位 agent
    # 的，不是房间的，结论 60）、这是谁对这个人形成的看法、以及写进谁的池子。
    agent = await _calling_agent(db, project_id, caller)
    # 这道闸只拦记忆。「memory 只记 repo 里查不到的」是记忆那一侧的判据（结论
    # 61），而 scope="everyone" 写的是文档：文档给人看、与 memory 正交，项目用
    # 什么技术栈、分工写在哪个文件里，恰恰是总览文档该说的话——repo 里写着并不
    # 让它少说一句。拿记忆的判据挡文档，等于把「写给所有人看」这个唯一的留痕动
    # 作从最典型的项目事实上收走。
    if scope != "everyone" and place is not None and agent is not None:
        hit = await already_in_repo(
            content,
            agent_checkout_search(
                db, place.room, agent.handle, harness_for(project.settings)
            ),
        )
        if hit is not None:
            raise ValidationError(
                f"这条事实 repo 里已经写着了（{hit.path}:{hit.line}）——"
                f"「{hit.text}」。记忆只记 repo 里查不到的东西；"
                "要让别人看见就改那个文件，不要在这里记一份会过期的副本。"
            )
    if scope == "user":
        owner = (body.get("owner") or "").strip()
        if not owner:
            raise ValidationError("owner 不能为空（个人记忆需要 owner）")
        await _authorize_personal_memory_owner(db, place, owner)
        viewer = await _agent_speaking(db, project, agent)
        await memory_store(db).remember(
            MemoryScope.user,
            user_scope_id(project_id, viewer.handle, owner),
            content,
            layer=layer,
        )
        return ok({"remembered": True, "layer": layer.value})
    if scope == "everyone":
        if caller is None:
            # 这一路写的是总览的实况文档，而这个端点的授权全在 `_authorized_place`
            # 里：不带 `topic`，`resolve` 与 `authorize_topic` 一次都不跑。落在记忆
            # 上时那只是自己池子里的一行，落在文档上就是替项目默认芝士往大家共看的
            # 那一份里添字，还在总览房间留一条「编辑了文档」。写入闸
            # （`cheese_token_gate`）只证明「这个项目的某张凭证」，连不代表任何参与
            # 者的项目级能力票也算数，所以「你在哪儿」这一句必须有人答。
            #
            # 不在这里拿总览房间另解析一次 actor：一张按房间签的每轮 token 去问总览
            # 房间，`_reject_out_of_scope_token` 会判它越界（403），于是每一次从线程
            # 里发出的 `remember --everyone` 都被拦下——那是把正当调用一起挡掉。带上
            # 地点，caller 就已经是这个项目里一个被授权的参与者。
            raise ForbiddenError(
                "写给所有人看的要带上 topic：平台据此确认你是这个项目里的人"
            )
        if layer is MemoryLayer.core:
            raise ValidationError(
                "写给所有人看的落在项目总览的实况文档里，文档没有核心记忆这一档"
            )
        # 总览是哪个房间只有项目行知道（同 `TopicService._overview_room`）。
        assert project.root_topic_id is not None  # create() 一定播种了总览。
        topics = TopicService(db)
        doc = await topics.get_doc(project.root_topic_id)
        writer = (await _agent_speaking(db, project, agent)).handle
        # 追加，不改写：这一份是大家共同在看的状态，芝士往上添一条观察，别人
        # 写在上面的话一个字都不动。文档冲突照旧由 `edit_doc` 的版本号挡。
        updated, _ = await topics.edit_doc(
            topic_id=project.root_topic_id,
            content=_with_fact_for_everyone(
                doc.content if doc else "", content, writer
            ),
            author=writer,
            expected_version=doc.doc_version if doc is not None else 0,
        )
        return ok({"documented": True, "doc_version": updated.doc_version})
    writer = await _agent_speaking(db, project, agent)
    await memory_store(db).remember(
        *memory_pool(project_id, writer), content, layer=layer
    )
    return ok({"remembered": True, "layer": layer.value})


@router.post("/{project_id}/memory/search")
async def search_memory(
    project_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """记忆检索 — used by the `cheese recall` CLI. Defaults to the pools this
    turn already reads; with scope="user"+owner it searches this agent's view
    of that one person. This is keyword matching ranked by query coverage, not
    semantic search — related, but not the same thing, which is why the CLI
    never promises 语义搜索."""
    from app.domain.memory.models import user_scope_id
    from app.domain.memory.pools import pools_for_turn
    from app.domain.memory.store import memory_store

    project = await ProjectService(db).get_or_404(project_id)
    caller = await _authorized_place(
        db, resolver, project_id, (body.get("topic") or "").strip()
    )
    place = caller[0] if caller else None
    query = (body.get("query") or "").strip()
    if not query:
        raise ValidationError("query 不能为空")
    store = memory_store(db)
    agent = await _calling_agent(db, project_id, caller)
    if (body.get("scope") or "project") == "user":
        owner = (body.get("owner") or "").strip()
        if not owner:
            raise ValidationError("owner 不能为空（个人记忆需要 owner）")
        await _authorize_personal_memory_owner(db, place, owner)
        viewer = await _agent_speaking(db, project, agent)
        hits = await store.search(
            MemoryScope.user, user_scope_id(project_id, viewer.handle, owner), query
        )
        return ok({"hits": [h.as_dict() for h in hits]})
    # `cheese recall` 查的就是这一轮注入时读的那几个池（`pools_for_turn`），一条
    # 不多一条不少。两边同一份清单，否则会出现「注入里提过池子还有 N 条，recall
    # 却查不到」——而注入那句话的全部作用就是让人来 recall。项目共看的那份状态
    # 不在这里：它是总览的实况文档（结论 7），每一轮本来就整份进提示词。
    #
    # 按分数合并，不按池子首尾相接：一条事实恰好落在哪个池里，说明不了它答这个
    # 问题答得多好，而调用方是从上往下读的。
    speaker = await _agent_speaking(db, project, agent)
    pools = pools_for_turn(
        project_id,
        speaker.handle,
        await TopicMemberService(db).people_handles(place.room_id)
        if place is not None
        else [],
    )
    hits: list = []
    for scope, scope_id in pools:
        hits.extend(await store.search(scope, scope_id, query))
    hits.sort(key=lambda h: -h.score)
    return ok({"hits": [h.as_dict() for h in hits]})


@router.get("/{project_id}/private-chat")
async def get_private_chat(
    project_id: uuid.UUID,
    user_handle: str,
    db: DbSession,
    resolver: ActorResolverDep,
    peer_handle: str | None = None,
    agent_handle: str | None = None,
) -> dict:
    """Get-or-create a 1:1 private chat (spec §1).

    With ``peer_handle`` it is a person-to-person DM between the two humans,
    shared by both regardless of who opens it first. Without, it is the member's
    1:1 with one AI teammate — ``agent_handle`` picks which, defaulting to the
    project's default teammate.
    """
    actor = await resolver.require_verified_caller(project_id=project_id)
    if actor.authenticated:
        await resolver.authorize_project(actor, project_id=project_id)
        participants = {user_handle, peer_handle} - {None}
        if actor.handle not in participants:
            raise ForbiddenError("只能打开自己参与的私聊")
    topic = await TopicService(db).get_or_create_private(
        project_id=project_id,
        user_handle=user_handle,
        peer_handle=peer_handle,
        agent_handle=agent_handle,
    )
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


@router.get("/{project_id}/forge")
async def get_project_forge(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    from app.domain.project.forge import binding_for_project

    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    binding = await binding_for_project(project_id, db)
    return ok(
        {
            "kind": binding.kind
            if binding
            else (project.settings or {}).get("forge_kind", "forgejo"),
            "connected": binding is not None,
            "repo": binding.repo if binding else None,
            "url": binding.url.removesuffix(".git") if binding else None,
        }
    )


@router.get("/{project_id}/forge-attribution")
async def get_forge_attribution(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    from app.domain.repository.identity import requester_credit_enabled

    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    return ok(
        {
            "requester_coauthor": (project.settings or {}).get(
                "forge_requester_coauthor"
            ),
            "effective": requester_credit_enabled(project.settings or {}),
            "deployment_default": settings.forge_attribution_default,
        }
    )


@router.put("/{project_id}/forge-attribution")
async def save_forge_attribution(
    project_id: uuid.UUID,
    body: ForgeAttributionUpdate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await MemberService(db).require_manager(project_id, actor)
    project = await ProjectService(db).get_or_404(project_id)
    values = dict(project.settings or {})
    if body.requester_coauthor is None:
        values.pop("forge_requester_coauthor", None)
    else:
        values["forge_requester_coauthor"] = body.requester_coauthor
    project.settings = values
    await db.flush()
    return await get_forge_attribution(project_id, db, resolver)


# --- Project main and native subagent model defaults ---


def _default_model_state(project_settings: dict | None) -> dict:
    from app.domain.agent_instance.configuration import model_choices, project_pool

    choices = model_choices(project_settings)
    chosen = (project_settings or {}).get("default_model")
    # 落在目录里才是「真的设了」——历史数据可能写过部署兜底算不出来的名字，
    # 那种情况按没设处理，由调用方决定要不要报。这里只读，不修。
    known_ids = {c["id"] for c in choices}
    effective = chosen if isinstance(chosen, str) and chosen in known_ids else None
    deployment_settings = dict(project_settings or {})
    deployment_settings.pop("default_model", None)
    deployment_choices = model_choices(deployment_settings)
    return {
        "model": effective,
        "subagent_model": (project_settings or {}).get("default_subagent_model"),
        "deployment_default": next(
            (c["id"] for c in deployment_choices if c["default"]), None
        ),
        "choices": choices,
        # 发现层（sync-agents）按池过滤目录：与准入同源的 project_pool,别让
        # 每个读目录的人自己从默认项反推（零默认的目录推不出来）。
        "pool": project_pool(project_settings),
        "can_manage": False,  # 由路由层按权限填
    }


@router.get("/{project_id}/default-model")
async def get_default_model(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    state = _default_model_state(project.settings)
    try:
        await MemberService(db).require_manager(project_id, actor)
        state["can_manage"] = True
    except ForbiddenError:
        state["can_manage"] = False
    return ok(state)


@router.put("/{project_id}/default-model")
async def save_default_model(
    project_id: uuid.UUID,
    body: ProjectDefaultModelUpdate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """设/清项目默认模型。设一个目录里没有的名字直接拒，不静默换池（I27）。"""
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await MemberService(db).require_manager(project_id, actor)
    project = await ProjectService(db).get_or_404(project_id)
    values = dict(project.settings or {})
    from app.domain.agent_instance.configuration import model_choices

    valid = {c["id"] for c in model_choices(values)}
    for field, key in (
        ("model", "default_model"),
        ("subagent_model", "default_subagent_model"),
    ):
        if field not in body.model_fields_set:
            continue
        chosen = getattr(body, field)
        if chosen is None:
            values.pop(key, None)
        elif chosen not in valid:
            raise ValidationError(f"当前项目无法使用模型 {chosen!r}，请选择可用模型")
        else:
            values[key] = chosen
    project.settings = values
    await db.flush()
    state = _default_model_state(project.settings)
    state["can_manage"] = True
    return ok(state)


# --- Compute pool (design §3): which machine runs this project's sandbox ---


@router.get("/{project_id}/compute-configs")
async def get_compute_configs(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    from app.domain.agent.device_hub import device_hub
    from app.domain.device.wiring import sql_device_service

    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    can_manage = True
    try:
        await MemberService(db).require_manager(project_id, actor)
    except ForbiddenError:
        can_manage = False
    devices = await sql_device_service(db).list_devices_for_project(project_id)
    return ok(
        {
            **project_configs(project.settings).model_dump(),
            "can_manage": can_manage,
            "devices": [
                {
                    "device_id": d.device_id,
                    "name": d.name,
                    "online": device_hub.is_online(d.device_id),
                }
                for d in devices
            ],
            "cloud_available": any(
                p.id == COMPUTE_CLOUD for p in compute_selectable(settings)
            ),
        }
    )


@router.put("/{project_id}/compute-configs")
async def save_compute_configs(
    project_id: uuid.UUID,
    body: ProjectComputeConfigs,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    await MemberService(db).require_manager(project_id, actor)
    for choice in [body.default, *body.favorites]:
        await validate_choice(db, project_id, choice)
        if choice.profile == COMPUTE_CLOUD:
            await MachineService(db).require_use_authority(project_id, actor)
    values = dict(project.settings or {})
    values.pop("compute_profile", None)
    values["compute_configs"] = body.model_dump()
    project.settings = values
    await db.flush()
    return ok(body.model_dump())


# --- 档位策略 (结论 3 后半 / 40 后半): 哪几档可以自己发生，超档怎么办 -----


@router.get("/{project_id}/tier-policy")
async def get_tier_policy(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """这个项目允许哪几档资源自己发生，以及超档怎么办。

    `tiers` 一并给出目录里现在存在的档位，所以调用方不必自己维护一份档位表——
    那正是闸门拒绝按型号列白名单的同一个理由（`domain/policy/gate.py`）。
    """
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    policy = gate.policy_of(project.settings)
    return ok(
        {
            gate.ALLOWED_TIERS_KEY: (
                None if policy.allowed_tiers is None else sorted(policy.allowed_tiers)
            ),
            gate.OVER_TIER_KEY: policy.over_tier,
            "tiers": sorted(
                {choice["tier"] for choice in model_choices(project.settings)}
                | set(COMPUTE_TIERS.values())
            ),
        }
    )


@router.put("/{project_id}/tier-policy")
async def set_tier_policy(
    project_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """改档位策略。只有项目管理者能改——它决定谁的点头才能放行一次调用。

    `allowed_tiers` 传 `null` 是不限档（默认），传一个列表是只有这几档可以自己
    发生。身上带的档位名不做存在性校验：目录里的档位随部署变（接一个新池就多一
    档），而一条指向不存在档位的策略只是更严，不会让任何调用悄悄放行。
    """
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await MemberService(db).require_manager(project_id, actor)
    project = await ProjectService(db).get_or_404(project_id)
    values = dict(project.settings or {})
    if gate.ALLOWED_TIERS_KEY in body:
        tiers = body.get(gate.ALLOWED_TIERS_KEY)
        if tiers is None:
            values.pop(gate.ALLOWED_TIERS_KEY, None)
        elif isinstance(tiers, list) and all(isinstance(t, str) for t in tiers):
            values[gate.ALLOWED_TIERS_KEY] = sorted({t.strip() for t in tiers if t})
        else:
            raise ValidationError("allowed_tiers 必须是字符串数组或 null")
    if gate.OVER_TIER_KEY in body:
        disposition = body.get(gate.OVER_TIER_KEY)
        if disposition not in gate.DISPOSITIONS:
            raise ValidationError(f"over_tier 只能是 {sorted(gate.DISPOSITIONS)} 之一")
        values[gate.OVER_TIER_KEY] = disposition
    project.settings = values
    await db.flush()
    return await get_tier_policy(project_id, db, resolver)


@router.get("/{project_id}/compute-profiles")
async def list_compute_profiles(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Available compute sources and the explicit project default."""
    configs = await get_compute_configs(project_id, db, resolver)
    current = configs["data"]["default"]["profile"]
    device_online = await project_device_online(db, project_id)
    profiles = [
        asdict(v) for v in compute_selectable(settings, device_online=device_online)
    ]
    return ok({"current": current, "profiles": profiles})


@router.put("/{project_id}/compute-profile")
async def set_compute_profile(
    project_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
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
    from app.domain.agent.compute_configs import standard_choice

    configs = project_configs(project.settings)
    configs.default = standard_choice(name)
    await save_compute_configs(project_id, configs, db, resolver)
    return ok({"current": name})


# --- Project stewardship: who answers for a project ---------------------------


async def require_project_steward(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> str:
    """The verified owner/lead of a project, or a 404 that hides it.

    People and agents need the same management role. A credential alone does
    not grant authority to change ownership or the project's checks.

    Returns the caller's handle so a route can record who acted.
    """
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    if not actor.authenticated:
        raise NotFoundError("Project not found")
    handle = actor.handle
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    if project.owner_handle == handle:
        return handle
    member = await MemberRepository(db).get(project_id=project_id, user_handle=handle)
    if member is not None and member.role == ProjectRole.lead:
        return handle
    # Conceal project existence from anonymous callers and outsiders.
    raise NotFoundError("Project not found")


@router.put("/{project_id}/owner")
async def set_project_owner(
    project_id: uuid.UUID,
    body: dict,
    db: DbSession,
    steward: Annotated[str, Depends(require_project_steward)],
) -> dict:
    """Hand the project to someone else — or claim it when nobody holds it.

    ``owner_handle`` had seven readers and exactly one writer: ``POST /projects``.
    A project created without one could therefore never acquire one, and on the
    dogfood project it never did (#315): the field sat NULL for six days while
    every one of those seven readers quietly fell back to ``lead``, collapsing
    every owner-level decision onto one person and reporting no error anywhere.

    The new owner must already be on the project roster. Not ceremony — the
    roster is what ``authorize_topic_access`` reads, so handing the project to
    someone outside it produces an owner who cannot open the project's topics,
    which is a worse state than the NULL this route exists to escape.
    """
    handle = str(body.get("owner_handle") or "").strip()
    if not handle:
        raise ValidationError("owner_handle 不能为空")
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    if handle != project.owner_handle:
        member = await MemberRepository(db).get(
            project_id=project_id, user_handle=handle
        )
        if member is None:
            raise ValidationError(
                f"{handle} 不是这个项目的成员——请先把 TA 加进项目成员，再转交"
            )
    previous = project.owner_handle
    project.owner_handle = handle
    await db.flush()
    # Ownership moves are rare, consequential, and (per #315) previously
    # impossible — worth a permanent record of who moved it and from what.
    logger.info(
        "project owner set project=%s from=%s to=%s by=%s",
        project_id,
        previous,
        handle,
        steward,
    )
    return ok(await _project_payload(db, project))


# --- Branch protection (issue #718): 平台侧的分支保护规则 ---------------------


def _branch_protection_payload(bp: BranchProtection) -> dict:
    return {
        "required_checks": [
            {"name": c.name, "paths": list(c.paths)} for c in bp.required_checks
        ],
        "strict": bp.strict,
        "dismiss_stale": bp.dismiss_stale,
        "auto_merge_allowed": bp.auto_merge_allowed,
        # None = unconfigured: the project's owner and leads may override.
        "override_handles": (
            list(bp.override_handles) if bp.override_handles is not None else None
        ),
        "approvals_required": bp.approvals_required,
        "default_reviewer": bp.default_reviewer,
    }


@router.get("/{project_id}/branch-protection")
async def get_branch_protection(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The project's branch-protection rules (issue #718), GitHub 那一页的顺序。

    平台补位 GitHub 判定不了的部分，所以规则存在这里；两块只读附注说明 GitHub
    那一侧的现实：``merge_method``（绑定项目从仓库设置读，未绑定固定 squash）和
    ``github_protection``（GitHub 自己开没开保护 —— 开了的话设置页把同名规则灰
    掉，两处都能改就是两套配置）。GitHub 查询失败一律降级成 unknown，绝不 500。
    """
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    bp = branch_protection_of(project)
    installation = await ProjectGitInstallationRepository(db).get_by_project(project_id)
    if installation is None:
        merge_method, gh = "squash", GITHUB_UNBOUND
    else:
        token: str | None = None
        try:
            token = await github_app_read_token_for_project(project_id, db)
        except Exception:  # noqa: BLE001 — display-only: degrade, never 500
            logger.warning(
                "branch-protection: token mint failed project=%s",
                project_id,
                exc_info=True,
            )
        merge_method, gh = await github_repo_snapshot(installation.repo, token)
    return ok(
        {
            **_branch_protection_payload(bp),
            "merge_method": merge_method,
            "github_protection": {
                "enforced": gh.enforced,
                "status": gh.status,
                "detail": gh.detail,
            },
        }
    )


_BRANCH_PROTECTION_KEYS = (
    "required_checks",
    "strict",
    "dismiss_stale",
    "auto_merge_allowed",
    "override_handles",
    "default_reviewer",
)


@router.put(
    "/{project_id}/branch-protection",
    dependencies=[Depends(require_project_steward)],
)
async def set_branch_protection(
    project_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Update branch-protection rules. Only the keys present in the body change.

    ``approvals_required`` predates this block and stays at
    ``settings["approvals_required"]`` — read and written here, never moved,
    never dual-written. Who may change review policy is the steward dependency's
    question, and it is a question about role: an owner or a lead, whoever they
    are.
    """
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    new_settings = {**(project.settings or {})}
    if any(key in body for key in _BRANCH_PROTECTION_KEYS):
        try:
            updated = apply_branch_protection_update(
                new_settings.get(BRANCH_PROTECTION_KEY), body
            )
        except ValueError as e:
            raise ValidationError(str(e)) from None
        if updated:
            new_settings[BRANCH_PROTECTION_KEY] = updated
        else:
            new_settings.pop(BRANCH_PROTECTION_KEY, None)  # all defaults again
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
    return ok(_branch_protection_payload(branch_protection_of(project)))


@router.get("/{project_id}/upstream")
async def get_project_upstream(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The GitHub repository selected for the installation flow."""
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    return ok({"url": (project.settings or {}).get("github_repository_url")})


@router.put("/{project_id}/upstream")
async def set_project_upstream(
    project_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Select a GitHub repository before binding its installation."""
    from app.domain.project.forge import binding_for_project
    from app.domain.review.github_pr import parse_github_repo

    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await MemberService(db).require_manager(project_id, actor)
    project = await ProjectService(db).get_or_404(project_id)
    if await binding_for_project(project_id, db) is not None:
        raise ConflictError("项目已连接代码仓库，暂不支持更换")
    if (project.settings or {}).get("forge_kind") != "github_app":
        raise ConflictError("这个项目由芝士托管，暂不支持切换到 GitHub")
    raw = str(body.get("url") or "").strip()
    parsed = parse_github_repo(raw) if raw else None
    if raw and parsed is None:
        raise ValidationError("请输入 GitHub 仓库地址")
    url = f"https://github.com/{parsed[0]}/{parsed[1]}" if parsed else None
    project.settings = {**(project.settings or {}), "github_repository_url": url}
    await db.flush()
    return ok({"url": url})
