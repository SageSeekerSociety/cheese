"""Project routes."""

import asyncio
import logging
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
    get_work_runner,
    project_device_online,
)
from app.api.response import ok, page
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.agent.chat import ChatService
from app.domain.agent.market import (
    COMPUTE_CLOUD,
    compute_default_name,
    compute_selectable,
    subscription_model_default,
    subscription_model_ids,
    subscription_model_listings,
)
from app.domain.agent.profiles import ProfileRegistry
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.agent_instance.schemas import (
    AgentInstanceCreate,
    AgentInstanceOut,
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
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.machine.services import MachineService
from app.domain.membership.repositories import MemberRepository
from app.domain.memory.models import MemoryScope
from app.domain.project.models import ProjectRole
from app.domain.project.repositories import ProjectRepository
from app.domain.project.schemas import (
    ProjectCreate,
    ProjectOut,
)
from app.domain.project.services import ProjectService
from app.domain.review.repositories import AcceptCardRepository
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

    `anonymous` is what an unidentified caller resolves to and 芝士's handles
    are agents; neither can hold owner authority, so neither counts as an owner
    even though both are non-empty strings.

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

QUALITY_GATE_COMMAND_MAX_CHARS = 4096


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
async def get_project(project_id: uuid.UUID, db: DbSession) -> dict:
    project = await ProjectService(db).get_or_404(project_id)
    return ok(ProjectOut.model_validate(project).model_dump(mode="json"))


def _agent_out(
    project_id: uuid.UUID, agent: ResolvedAgent, *, is_default: bool
) -> dict:
    return AgentInstanceOut(
        id=agent.instance_id,
        project_id=project_id,
        handle=agent.handle,
        type_name=agent.type_name,
        display_name=agent.display_name,
        is_default=is_default,
        configured=agent.instance_id is not None,
    ).model_dump(mode="json")


@router.get("/{project_id}/agents")
async def list_project_agents(project_id: uuid.UUID, db: DbSession) -> dict:
    """The agents this project has, and which one a new topic gets.

    A project that never configured one is not empty: it still has an implicit
    芝士 (``configured: false``), and that agent owns a real memory pool. Hiding
    it would make the settings page claim there is no agent while one is
    plainly working in every room.
    """
    project = await ProjectService(db).get_or_404(project_id)
    service = AgentInstanceService(db)
    default = await service.for_project(project)
    rows = await service.list_for_project(project_id)
    items = [
        _agent_out(
            project_id,
            AgentInstanceService.resolved(row),
            # A row under the implicit handle IS the project's 芝士 — same
            # handle, therefore the same memory pool — so it holds the default
            # even before anything points at it.
            is_default=row.id == project.default_agent_instance_id
            or (
                project.default_agent_instance_id is None
                and row.handle == IMPLICIT_DEFAULT.handle
            ),
        )
        for row in rows
    ]
    # Synthesized only when nothing materialized it yet. Listing both would show
    # two agents under one handle, reading as two teammates where there is one.
    if default.instance_id is None and not any(item["is_default"] for item in items):
        items.insert(0, _agent_out(project_id, default, is_default=True))
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
        handle=body.handle or body.type_name or IMPLICIT_DEFAULT.handle,
        type_name=body.type_name,
        display_name=body.display_name,
    )
    await db.flush()
    return ok(
        _agent_out(
            project_id, AgentInstanceService.resolved(instance), is_default=False
        )
    )


@router.put("/{project_id}/default-agent")
async def set_project_default_agent(
    project_id: uuid.UUID, body: ProjectDefaultAgentIn, db: DbSession
) -> dict:
    """Which agent a new topic in this project gets.

    Two ways in, because they are two different intents. ``instance_id`` picks a
    different agent — a different memory pool. ``type_name`` re-skins the one
    the project already has, which is what "which persona does 芝士 wear here"
    means: the pool it has been filling stays its own.
    """
    project = await ProjectService(db).get_or_404(project_id)
    service = AgentInstanceService(db)
    if body.instance_id is not None:
        instance = await service.get_in_project(
            project_id=project_id, instance_id=body.instance_id
        )
        agent = await service.set_project_default(project, instance)
    else:
        instance = await service.materialize_default(project)
        await service.set_type(instance, body.type_name)
        await db.flush()
        agent = await service.for_project(project)
    return ok(_agent_out(project_id, agent, is_default=True))


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


@router.get("/{project_id}/tasks")
async def list_project_tasks(project_id: uuid.UUID, db: DbSession) -> dict:
    """Every thread in the project, each with the card it currently rides on.

    The rail draws rooms and the work inside them, so it needs both halves at
    once. Two round trips, not two per room and one per thread: a project here
    already holds ~170 rooms, and the per-room shape (`/topics/{id}/tasks`)
    would make painting one sidebar 170 requests before a single PR badge.

    `card` is the newest accept card ON THAT THREAD, narrowed to what a rail row
    can show — where the work stands and the PR it rides on. Null for a thread
    that has not been filed for acceptance, which is most of them while the work
    is still going.
    """
    await ProjectService(db).get_or_404(project_id)
    tasks = await TaskRepository(db).list_for_project(project_id)
    cards = await AcceptCardRepository(db).latest_by_task([t.id for t in tasks])
    items = []
    for task in tasks:
        card = cards.get(task.id)
        items.append(
            {
                **TaskOut.model_validate(task).model_dump(mode="json"),
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


async def _authorized_memory_topic(
    db: DbSession,
    resolver: ActorResolverDep,
    project_id: uuid.UUID,
    topic_raw: str,
) -> Place | None:
    """Resolve and authorize the body-carried place, when present.

    A place, not a room: `cheese remember` is run by whoever is doing the work,
    and that is usually a thread. Resolving only rooms answered 404 for the one
    caller this endpoint exists for.
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
    # Two different ids on purpose. A per-turn token is scoped to the PLACE it
    # was minted for, so that is what identity is checked against — handing it
    # the room would read a thread's token as out-of-scope and erase its author.
    # Access, though, is the room's roster: threads do not have one.
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=place.id, project_id=project_id
    )
    await resolver.authorize_topic(actor, project_id=project_id, topic_id=place.room_id)
    return place


async def _agent_memory_scope(
    db: DbSession, project_id: uuid.UUID, place: Place | None
) -> tuple[MemoryScope, str] | None:
    """Where the agent working in ``place`` writes what it learns.

    Keyed by the AGENT, not by the room: the same 芝士 moving between rooms of
    one project keeps one pool, which is the whole point of an instance owning
    its memory. Returns ``None`` when no usable place was supplied, so the
    caller falls back to the shared project pool.

    A thread is asked about its own row: it was handed the room's agent when
    the work went out, so what it learns lands in the pool the room reads back
    — which is the entire reason the room dispatched it.
    """
    if place is None:
        return None
    project = await ProjectService(db).get_or_404(project_id)
    owner = place.task if place.task is not None else place.room
    agent = await AgentInstanceService(db).for_topic(owner, project)
    return memory_pool(project_id, agent)


async def _agent_memory_read_scopes(
    db: DbSession, project_id: uuid.UUID, place: Place | None
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
    if place is None:
        return []
    agent_scope = await _agent_memory_scope(db, project_id, place)
    if agent_scope is None:
        return []
    scopes = [agent_scope]
    legacy = legacy_topic_pool(project_id, place.room_id)
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
    place = await _authorized_memory_topic(
        db, resolver, project_id, (body.get("topic") or "").strip()
    )
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
    agent_scope = await _agent_memory_scope(db, project_id, place)
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
    place = await _authorized_memory_topic(
        db, resolver, project_id, (body.get("topic") or "").strip()
    )
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
    for scope in await _agent_memory_read_scopes(db, project_id, place):
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
) -> dict:
    """Get-or-create a 1:1 private chat (spec §1).

    Without ``peer_handle`` this is the member's 1:1 with 芝士. With
    ``peer_handle`` it is a person-to-person DM between the two humans, shared
    by both regardless of who opens it first.
    """
    actor = await resolver.require_verified_caller(project_id=project_id)
    if actor.authenticated:
        await resolver.authorize_project(actor, project_id=project_id)
        participants = {user_handle, peer_handle} - {None}
        if actor.is_agent or actor.handle not in participants:
            raise ForbiddenError("只能打开自己参与的私聊")
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
    if name == COMPUTE_CLOUD:
        actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
        await MachineService(db).require_create_authority(project_id, actor)
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


# --- Project stewardship: who answers for a project ---------------------------


async def require_project_steward(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> str:
    """The verified human owner/lead of a project, or a 404 that hides it.

    A sandbox-scoped agent token is deliberately not accepted: an agent that
    could configure the command judging its own work has a review bypass (and,
    before gate isolation, a host-command primitive), and an agent that could
    reassign ``owner_handle`` could hand itself the project.

    Returns the caller's handle so a route can record who acted.
    """
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    if not actor.authenticated or actor.via != "token" or actor.is_agent:
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


# --- Quality gate (spec §4.4/§9, eval C2): 平台硬门 configuration -------------


async def require_quality_gate_admin(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> None:
    """Only a verified human project owner/lead may configure executable policy."""
    await require_project_steward(project_id, db, resolver)


@router.get("/{project_id}/quality-gate")
async def get_quality_gate(project_id: uuid.UUID, db: DbSession) -> dict:
    """The project's 硬门 settings: `check_command` and `approvals_required`
    (distinct approvals an accept needs; default 1).

    采纳即合并退役闸门 (docs/accept-is-merge.md #296, stage 1): `check_command`
    is RETIRED. It used to run in the topic workspace before a card reached the
    reviewer; that mechanism is gone — a card is the view of a PR and real CI on
    that PR decides. The value is still stored and returned (round-trip stays
    working, a later stage clears it) but nothing runs it any more.
    `approvals_required` is unaffected."""
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
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
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
