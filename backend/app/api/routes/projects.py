"""Project routes."""

import asyncio
import logging
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
    get_work_runner,
    project_device_online,
)
from app.api.response import ok, page
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import (
    ForbiddenError,
    GatewayUnavailableError,
    NotFoundError,
    ValidationError,
)
from app.domain.agent.chat import ChatService
from app.domain.agent.compute_configs import (
    ProjectComputeConfigs,
    project_configs,
    validate_choice,
)
from app.domain.agent.github_app import (
    GitHubAppError,
    github_app_read_token_for_project,
)
from app.domain.agent.market import (
    COMPUTE_CLOUD,
    compute_default_name,
    compute_selectable,
)
from app.domain.agent.profiles import ProfileRegistry
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.agent_instance.configuration import AgentConfiguration
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_instance.schemas import (
    AgentInstanceCreate,
    AgentInstanceOut,
    AgentInstanceUpdate,
    ProjectDefaultAgentIn,
)
from app.domain.agent_instance.services import (
    IMPLICIT_DEFAULT,
    AgentInstanceService,
    ResolvedAgent,
    legacy_topic_pool,
    memory_pool,
)
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.identity.actor import Actor
from app.domain.identity.handles import agent_instance_handle, looks_like_agent_handle
from app.domain.machine.limits import get_machine_limit
from app.domain.machine.services import MachineService
from app.domain.membership.repositories import MemberRepository
from app.domain.membership.services import MemberService
from app.domain.memory.models import MemoryScope
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
    ProjectCreate,
    ProjectOut,
)
from app.domain.project.services import ProjectService
from app.domain.review.repositories import AcceptCardRepository
from app.domain.room_task import presentation
from app.domain.room_task.place import Place
from app.domain.room_task.repositories import TaskRepository
from app.domain.room_task.schemas import TaskOut
from app.domain.topic.schemas import TopicOut
from app.domain.topic.services import TopicService
from app.domain.workspace import service as ws
from app.domain.workspace import upstream_conflict

logger = logging.getLogger("cheesex.projects")


def _is_a_real_person(handle: str | None) -> bool:
    """Could this handle ever match a human account?

    `anonymous` is what an unidentified caller resolves to: nobody, so no owner.
    An agent handle is somebody, and can hold a project role like anybody else —
    it is excluded here only because this answers "is there a person to name in
    the log", and naming 芝士 as the person answers nothing.

    Advisory only — this decides whether to LOG, never whether to allow. That
    is why `looks_like_agent_handle` is fair game here despite its docstring
    forbidding it in authorization: nothing downstream branches on the answer.
    """
    return (
        bool(handle) and handle != "anonymous" and not looks_like_agent_handle(handle)
    )


router = APIRouter(prefix="/projects", tags=["projects"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
Registry = Annotated[ProfileRegistry, Depends(get_profile_registry)]


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
    if not _is_a_real_person(owner_handle):
        # Silence is how this got expensive (#315). A project whose owner is not
        # a real person can be repaired — PUT /{id}/owner exists now — but
        # nothing else in the system will ever mention it: all seven readers of
        # the field fall back to `lead` without erroring, so the gap surfaces
        # only as "why can only one person do anything here", six days later.
        #
        # The check is "a real person", not "not empty", because the empty case
        # is no longer the one that happens. `resolve()` hands back the literal
        # handle `anonymous` rather than nothing, so an unidentified creator now
        # produces a *populated* owner column that still matches no user — the
        # same collapse onto `lead`, wearing a value.
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
        team_id=body.team_id,
        external_task_id=body.external_task_id,
    )
    # The caller can create a room as soon as this response arrives; the
    # request-scoped dependency commits only after sending the response.
    await db.commit()
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
async def get_project(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    return ok(ProjectOut.model_validate(project).model_dump(mode="json"))


def _holds_the_default(project: Project, row: AgentInstance) -> bool:
    """Whether this row is what a new topic in the project gets.

    Two ways to be it, and both have to be checked in every place that reports
    it or the same agent comes back ``is_default`` from one route and not from
    another: the project points at it, or it IS the project's 芝士 — same
    handle, therefore the same memory pool — which holds the default even
    before anything points at it. A retired row holds nothing.
    """
    if row.id == project.default_agent_instance_id:
        return True
    return (
        project.default_agent_instance_id is None
        and row.is_active
        and row.handle == IMPLICIT_DEFAULT.handle
    )


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
        seat_handle=(
            agent_instance_handle(agent.instance_id) if agent.instance_id else None
        ),
        type_name=agent.type_name,
        display_name=agent.display_name,
        configuration=AgentConfiguration.model_validate(agent.configuration),
        is_default=is_default,
        configured=agent.instance_id is not None,
        is_active=is_active,
    ).model_dump(mode="json")


@router.get("/{project_id}/agents")
async def list_project_agents(project_id: uuid.UUID, db: DbSession) -> dict:
    """The project's saved agents, including its default for new rooms."""
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
    project_id: uuid.UUID, body: AgentInstanceCreate, db: DbSession
) -> dict:
    """Add an agent to this project. It starts with an empty memory pool."""
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


@router.get("/{project_id}/agent-options")
async def project_agent_options(project_id: uuid.UUID, db: DbSession) -> dict:
    from app.domain.agent_instance.configuration import harness_choices, model_choices

    project = await ProjectService(db).get_or_404(project_id)
    choices = model_choices(project.settings)
    # Every harness this deployment can actually run here, each carrying the
    # models it can be pointed at. The editor picks the harness and filters the
    # model list by it — the direction the constraint really runs, so nothing
    # downstream has to restate which pairs are legal.
    harnesses = harness_choices(project.settings)
    return ok(
        {
            "harness": {
                "state": "choosable" if harnesses else "unavailable",
                "choices": harnesses,
                "reason": ""
                if harnesses
                else "当前项目没有可用的运行方式，请检查模型服务",
                "note": "",
            },
            "model": {
                "state": "choosable" if choices else "unavailable",
                "choices": choices,
                "reason": "" if choices else "当前项目没有可用模型，请检查模型服务",
                "note": "",
            },
        }
    )


@router.put("/{project_id}/agents/{agent_id}")
async def update_project_agent(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    body: AgentInstanceUpdate,
    db: DbSession,
) -> dict:
    """Edit one agent's name and saved configuration.

    ``handle`` is not editable and is not accepted here: it keys the memory
    pool, so changing it would hand the agent an empty one and orphan
    everything it had learned in this project.
    """
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
    project_id: uuid.UUID, agent_id: uuid.UUID, db: DbSession
) -> dict:
    """Retire an agent — not a delete.

    The rooms already working with it carry on and its memory is kept; it just
    stops being offered for new work. The response says ``deleted`` because
    that is the shape a DELETE returns everywhere here, not because a row went
    away.
    """
    project = await ProjectService(db).get_or_404(project_id)
    service = AgentInstanceService(db)
    instance = await service.get_in_project(project_id=project_id, instance_id=agent_id)
    await service.deactivate(project, instance)
    return ok({"deleted": True})


@router.put("/{project_id}/default-agent")
async def set_project_default_agent(
    project_id: uuid.UUID, body: ProjectDefaultAgentIn, db: DbSession
) -> dict:
    """Select the existing agent that new rooms start with."""
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
    data = ws.read_library_file(project_id, name)
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
    files = ws.list_library_files(project_id)
    return ok(page(files, len(files)))


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
    ws.delete_library_file(project_id, _library_path(path))
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


@router.get("/{project_id}/tasks")
async def list_project_tasks(
    project_id: uuid.UUID,
    db: DbSession,
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


async def _agent_memory_scope(
    db: DbSession, project_id: uuid.UUID, caller: tuple[Place, Actor] | None
) -> tuple[MemoryScope, str] | None:
    """Where the agent that is calling writes what it learns.

    Keyed by the AGENT, not by the room: a room seats any number of teammates,
    and the one running `cheese remember` is the one on the token, so its notes
    go to its own pool wherever it is working — the same 芝士 moving between
    rooms keeps one pool. A token that names no saved teammate (an older one, a
    DM's) writes as the place's default: the DM's own teammate, else the
    project's. Returns ``None`` when no usable place was supplied, so the
    caller falls back to the shared project pool.
    """
    if caller is None:
        return None
    place, actor = caller
    project = await ProjectService(db).get_or_404(project_id)
    agents = AgentInstanceService(db)
    agent = await agents.for_seat_handle(
        project, actor.handle if actor.is_agent else None
    )
    if agent is None:
        agent = await agents.for_topic(place.room, project)
    return memory_pool(project_id, agent)


async def _agent_memory_read_scopes(
    db: DbSession, project_id: uuid.UUID, caller: tuple[Place, Actor] | None
) -> list[tuple[MemoryScope, str]]:
    """Every pool a read on behalf of ``place`` should cover.

    The agent's own pool, plus the pool this room filled back when memory was
    keyed by the room. Writes go to the first alone; the second is a read-only
    tail so that repointing memory at the agent does not read as amnesia in
    every room that had already learned something.

    The legacy tail is the ROOM's, even when a thread is asking: that pool was
    filled when work was a room of its own, so keying it by the thread would
    look up an id nothing ever wrote under.
    """
    if caller is None:
        return []
    agent_scope = await _agent_memory_scope(db, project_id, caller)
    if agent_scope is None:
        return []
    scopes = [agent_scope]
    legacy = legacy_topic_pool(project_id, caller[0].room_id)
    if legacy != agent_scope:
        scopes.append(legacy)
    return scopes


def _authorize_personal_memory_owner(place: Place | None, owner: str) -> None:
    """个人记忆 lives in a private chat, and a private chat is a room — so this
    asks the room even when a thread inside it is the caller."""
    if place is None:
        return
    room = place.room
    participants = {room.private_owner, room.private_peer} - {None}
    if not room.is_private or owner not in participants:
        raise ForbiddenError("只能在该成员自己的私聊中读写个人记忆")


@router.post("/{project_id}/memory")
async def add_memory(
    project_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """记入记忆 — used by the `cheese remember` CLI. With a ``topic`` it writes
    the acting 芝士's own memory for this project; with scope="user"+owner it
    writes that member's personal memory (private chat, spec §8.4 个人记忆跟着
    人走). Without either it falls back to the shared project pool.

    ``layer="core"`` buys a seat in every future prompt instead of a place in
    the pool that gets retrieved on demand — see MemoryLayer."""
    from app.domain.memory.models import MemoryLayer, MemoryScope
    from app.domain.memory.store import memory_store

    await ProjectService(db).get_or_404(project_id)
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
    if (body.get("scope") or "project") == "user":
        owner = (body.get("owner") or "").strip()
        if not owner:
            raise ValidationError("owner 不能为空（个人记忆需要 owner）")
        _authorize_personal_memory_owner(place, owner)
        await memory_store(db).remember(MemoryScope.user, owner, content, layer=layer)
        return ok({"remembered": True, "layer": layer.value})
    agent_scope = await _agent_memory_scope(db, project_id, caller)
    if agent_scope is not None:
        await memory_store(db).remember(*agent_scope, content, layer=layer)
    else:
        await memory_store(db).remember(
            MemoryScope.project, str(project_id), content, layer=layer
        )
    return ok({"remembered": True, "layer": layer.value})


@router.post("/{project_id}/memory/search")
async def search_memory(
    project_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """记忆检索 — used by the `cheese recall` CLI. Defaults to project memory;
    with scope="user"+owner it searches that member's personal memory. On the
    OpenViking backend this is semantic search returning L0 abstracts; the flat
    DB backend degrades to keyword matching ranked by query coverage — related,
    but not the same thing, which is why the CLI never promises 语义搜索."""
    from app.domain.memory.models import MemoryScope
    from app.domain.memory.store import memory_store

    await ProjectService(db).get_or_404(project_id)
    caller = await _authorized_place(
        db, resolver, project_id, (body.get("topic") or "").strip()
    )
    place = caller[0] if caller else None
    query = (body.get("query") or "").strip()
    if not query:
        raise ValidationError("query 不能为空")
    store = memory_store(db)
    if (body.get("scope") or "project") == "user":
        owner = (body.get("owner") or "").strip()
        if not owner:
            raise ValidationError("owner 不能为空（个人记忆需要 owner）")
        _authorize_personal_memory_owner(place, owner)
        hits = await store.search(MemoryScope.user, owner, query)
        return ok({"hits": [h.as_dict() for h in hits]})
    # The agent's own memory, the pool this room filled before memory followed
    # the agent, and the shared pool — the last two read-only tails of earlier
    # keyings. Merged on score, not concatenated by pool: which pool a fact
    # happens to sit in says nothing about how well it answers the question, and
    # the caller reads top-down.
    hits = []
    for scope in await _agent_memory_read_scopes(db, project_id, caller):
        hits.extend(await store.search(*scope, query))
    hits.extend(await store.search(MemoryScope.project, str(project_id), query))
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
    return ok(ProjectOut.model_validate(project).model_dump(mode="json"))


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
async def get_branch_protection(project_id: uuid.UUID, db: DbSession) -> dict:
    """The project's branch-protection rules (issue #718), GitHub 那一页的顺序。

    平台补位 GitHub 判定不了的部分，所以规则存在这里；两块只读附注说明 GitHub
    那一侧的现实：``merge_method``（绑定项目从仓库设置读，未绑定固定 squash）和
    ``github_protection``（GitHub 自己开没开保护 —— 开了的话设置页把同名规则灰
    掉，两处都能改就是两套配置）。GitHub 查询失败一律降级成 unknown，绝不 500。
    """
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
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """同步上游: fetch + merge the upstream default branch into the project base.
    Conflicts abort cleanly and come back as {"synced": false, "reason": ...} —
    and, when we know who asked, 芝士 is dispatched at the materialized conflict
    so that report is a starting point instead of a dead end (spec §6.3, same
    contract as 采纳冲突 in routes/accept.py)."""
    await ProjectService(db).get_or_404(project_id)
    # The App's token for a bound project, nothing for an unbound one: the
    # fetch runs on the platform's own identity or on none.
    try:
        token = await github_app_read_token_for_project(project_id, db)
    except GitHubAppError as exc:
        raise GatewayUnavailableError(str(exc)) from exc
    result = await asyncio.to_thread(ws.sync_upstream, project_id, token=token)
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
