"""Project routes."""

import asyncio
import logging
import uuid
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
)
from app.api.place import project_reader
from app.api.response import ok, page
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import (
    AuthenticationRequiredError,
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
from app.domain.membership.services import MemberService
from app.domain.policy import gate
from app.domain.preview.office import (
    OfficeRenderFailed,
    OfficeRenderUnavailable,
    is_renderable,
    render_to_pdf,
)
from app.domain.project import artifacts
from app.domain.project.models import Project
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
from app.domain.room_task.repositories import TaskRepository
from app.domain.room_task.schemas import TaskOut
from app.domain.shell.catalog import Shell
from app.domain.shell.schemas import ShellOut
from app.domain.shell.service import effective_shells
from app.domain.team.services import team_service
from app.domain.topic import naming
from app.domain.topic.schemas import TopicOut
from app.domain.topic.services import TopicService
from app.domain.user.services import user_by_handle

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


async def _require_team_membership(db: DbSession, who: Actor, team_id: int) -> None:
    """把项目生在一个团队里的，只能是那个团队的人。

    ``body.team_id`` 以前原样送到 ``ProjectService.create``，于是任何人——包括一个
    什么凭据都没带的调用方——都能把项目种进别人的团队：它会出现在那个团队的
    ``GET /projects?team_id=`` 列表里，挂着那个团队的名字和调用方自己的
    ``owner_handle``。团队域的路由用 ``require_permission(Action.X, Resource.Y,
    "teamId")`` 回答同一个问题；这里团队是**请求体**里给的、不是路径里的，所以同一个
    判断得显式做一遍。

    和 ``ActorResolver.authorize_team`` 的关键区别：这里**不挂**
    ``authz_enforce_topic_access`` 那个开关。开关管的是「谁能**读**别人项目的对话」，
    一个运维把它关掉，不该顺手把「写进这一行的那支外键」也变成不校验——那不是放宽
    可见性，那是让一次落库失去完整性。

    正常创建路径一点不受影响：前端送来的 ``team_id`` 永远是调用者自己的团队；个人项目
    根本不送 ``team_id``（由 ``_resolve_personal_team_id`` 从所有者推出来）。这里挡住的
    是「点名一个自己不在的团队」，以及按构造不在任何团队里的匿名调用方。
    """
    if not who.authenticated:
        raise AuthenticationRequiredError("登录后才能把项目建在团队里")
    if who.user_id is None or not await team_service(db).is_team_member(
        team_id, who.user_id
    ):
        raise ForbiddenError("你不是这个团队的成员，不能把项目建在这个团队里")


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
    # 项目归团队 (v4) 的那支外键也是**请求体**给的，所以它跟 owner_handle 一样要在这里
    # 过一道：问的不是「这个团队在不在」，是「你是不是这个团队的人」。
    if body.team_id is not None:
        await _require_team_membership(db, who, body.team_id)
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
        # nothing else in the system will ever mention it: every reader of the
        # field falls back to the team's admins without erroring, so the gap
        # surfaces only as "why can only they do anything here", days later.
        #
        # The check covers `anonymous` as well as empty, because the empty case
        # is no longer the one that happens. `resolve()` hands back the literal
        # handle `anonymous` rather than nothing, so an unidentified creator now
        # produces a *populated* owner column that still matches no user — the
        # same collapse onto the team's admins, wearing a value.
        #
        # It does NOT ask whether the owner is a person. An agent instance is a
        # participant like anybody else, so a handle
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
        intent=body.intent,
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
    payload = await _project_payload(db, project)
    # Whether this caller runs the project's membership: its owner, or an
    # owner/admin of its team. The members page shows invite/remove by it.
    payload["can_manage_members"] = actor.authenticated and await MemberService(
        db
    ).manages(project_id, actor.handle)
    return ok(payload)


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
    消息带上的资料时，只能自己来取（那时带着它干活的那个话题，见 `authorized_place`）。
    """
    await ProjectService(db).get_or_404(project_id)
    await project_reader(db, resolver, project_id, topic)
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


@router.get("/{project_id}/library")
async def list_library(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep, topic: str = ""
) -> dict:
    """资料库：用户给这个项目的文件，按原名，每个房间都引用得到。

    Project-level on purpose — 「上周那份预算表」is a sentence someone says in a
    room that has never seen that file."""
    await ProjectService(db).get_or_404(project_id)
    await project_reader(db, resolver, project_id, topic)
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
    await project_reader(db, resolver, project_id, topic)
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
    await project_reader(db, resolver, project_id, topic)
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
    await project_reader(db, resolver, project_id, topic)
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
    await project_reader(db, resolver, project_id, topic)
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
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    topic: str = "",
) -> dict:
    """决策记录 (spec §7.1): project-wide decision blocks, each traceable to its
    source topic via topic_id.

    These are the project's own words, not metadata about it — the same content
    ``/topics`` has always guarded.

    ``topic`` is the caller naming its place, and it is how 芝士 reads this at
    all (``project_reader``): a per-turn credential is minted for one turn in
    one room, so a bare ``authorize_project`` refuses it — which left the one
    caller that WRITES decisions (``POST /topics/{id}/decision``, the
    ``cheese decision`` CLI) unable to read a single one back. Its own room's
    blocks were reachable; the project's record was not."""
    await project_reader(db, resolver, project_id, topic)
    await ProjectService(db).get_or_404(project_id)
    blocks = await BlockRepository(db).list_by_kind_for_project(
        project_id, BlockKind.decision
    )
    items = [BlockOut.model_validate(b).model_dump(mode="json") for b in blocks]
    return ok(page(items, len(items)))


@router.get("/{project_id}/weeklies")
async def list_weeklies(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    topic: str = "",
) -> dict:
    """周报集 (spec §7.1): project-wide weekly blocks, newest first.

    Each carries the stretch it covers in `meta` (`since`/`until`). A weekly
    report says what happened over a piece of time rather than what the project
    looks like right now, so that window is what tells two of them apart.

    Same shape as /decisions and for the same reason: these are the project's
    own words, and each is traceable to the room it was written in via
    `topic_id` — including the same ``topic`` place, so the caller that writes
    a weekly (``POST /topics/{id}/weekly``) can read the set back."""
    await project_reader(db, resolver, project_id, topic)
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
    topic: str = "",
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
    await project_reader(db, resolver, project_id, topic)
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


@router.post("/{project_id}/memory")
async def add_memory(
    project_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """写记忆的旧入口 —— 已关闭，而且明说。

    这条端点是 `cheese_remember` 的后端。它写的是旧的条目池（`memory_entries`，
    按 agent 分池）；记忆换成「一条记忆一个 markdown 文件」之后（`MemoryFileStore`），
    那一份不再注入任何地方——它的索引不进提示词、它的正文没有读点。再往后端写只会
    得到一句「已记入」，而这条记忆以后谁都读不到：**静默丢失**。

    所以这里一律拒绝，并在这句话里说清该写哪儿。旧会话（上下文里还留着
    `cheese_remember` 那张工具表）里还在调它的调用方，读到的就是这句。

    参数一个都不看：这条路的授权、作用域、layer 现在都没有意义——它不是
    「写错了」而是「不该往这里写」，答一个「你没权限」只会把人引去要权限。
    """
    raise ValidationError(
        "记忆改为直接写 `~/.cheese/memory/` 下的文件：一条记忆一个 markdown "
        "文件（带 name/description/type 的 frontmatter），再在 `MEMORY.md` 里加一行"
        "指针。见系统提示里的「记忆」一节。`cheese_remember` 已停用——它写的是旧的"
        "条目池，那一份已经不再注入任何地方，写进去的事实以后读不到。"
    )


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
    from app.domain.machine.session_work import project_distribution

    return ok(
        {
            **project_configs(project.settings).model_dump(),
            "can_manage": can_manage,
            # Where the project's agents are working now; the default only
            # decides for agents that have not started.
            "distribution": await project_distribution(db, project_id),
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
    await validate_choice(db, project_id, body.default)
    if body.default.profile == COMPUTE_CLOUD:
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


@router.get("/{project_id}/topic-naming")
async def get_topic_naming(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """话题命名: ``auto`` (the platform names rooms and renames them when their
    direction changes; the default) or ``manual`` (rooms are named by people).
    See ``topic/naming.py``."""
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    can_manage = True
    try:
        await MemberService(db).require_manager(project_id, actor)
    except ForbiddenError:
        can_manage = False
    return ok(
        {
            "mode": naming.naming_mode(project.settings),
            "available": naming.available(),
            "can_manage": can_manage,
        }
    )


@router.put("/{project_id}/topic-naming")
async def set_topic_naming(
    project_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Switch the project's rooms between automatic and manual naming. Rooms a
    person named keep their names either way."""
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await MemberService(db).require_manager(project_id, actor)
    mode = body.get("mode")
    if mode not in naming.MODES:
        raise ValidationError(f"mode 只能是 {list(naming.MODES)} 之一")
    project = await ProjectService(db).get_or_404(project_id)
    project.settings = {**(project.settings or {}), naming.SETTINGS_KEY: mode}
    await db.flush()
    return await get_topic_naming(project_id, db, resolver)


# --- Project stewardship: who answers for a project ---------------------------


async def require_project_steward(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> str:
    """A verified manager of the project — its owner, or an owner/admin of its
    team — or a 404 that hides it.

    People and agents need the same standing. A credential alone does
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
    if await MemberService(db).manages(project_id, handle):
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
    every one of those readers quietly fell back to someone else, collapsing
    every owner-level decision onto them and reporting no error anywhere.

    The new owner must be on the project's team. Not ceremony — someone outside
    it would own a project they cannot open, which is a worse state than the
    NULL this route exists to escape.
    """
    handle = str(body.get("owner_handle") or "").strip()
    if not handle:
        raise ValidationError("owner_handle 不能为空")
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    if handle != project.owner_handle:
        # The project is its team's; its owner is someone from that team, not an
        # external member who sees this one project and nothing else of it.
        user = await user_by_handle(db, handle)
        if user is None or not await team_service(db).is_team_member(
            project.team_id, user.id
        ):
            raise ValidationError(
                f"{handle} 不是这个项目所属团队的成员——请先把 TA 加进团队，再转交"
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
    question: the project's owner, or an owner/admin of its team.
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
