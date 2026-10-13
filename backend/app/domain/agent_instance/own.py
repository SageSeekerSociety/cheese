"""A member's own coding agent, as a teammate in a project (#2991).

The row that makes a project's agent someone's (`OwnAgent`) is read in many
places — where its session runs, what it may be charged, who may call it — and
every one of them asks the same question: is the agent acting here somebody's
own, and whose. This is that one answer.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import listing as sentence_list
from app.core.sentences import say
from app.domain.agent_instance.models import (
    AgentInstance,
    NameSource,
    OwnAgent,
    OwnCallers,
)
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.device.login_kind import ConnectionKind, connection_kind
from app.domain.device.models import DeviceClaudeLoginRow, DeviceRow
from app.domain.identity.handles import agent_instance_handle, looks_like_agent_handle
from app.domain.user.models import User
from app.domain.user.services import faces_by_handle, user_by_handle

#: The project setting that lets members bring their own agent in. Off unless a
#: manager turns it on: the project's content leaves through a member's own
#: model account only where someone who manages the project chose that.
SETTING = "allow_own_agents"

#: The kinds of model connection (`ConnectionKind`) a project lets its members'
#: own agents use, as a list. Absent, every kind is allowed.
KINDS_SETTING = "own_agent_kinds"

#: How a room names each kind (catalog ``roomNotice``).
_KIND_WORDS = {
    ConnectionKind.subscription: "ownAgentKindSubscription",
    ConnectionKind.api_key: "ownAgentKindApiKey",
    ConnectionKind.model_service: "ownAgentKindModelService",
}


def allows_own_agents(project_settings: dict[str, Any] | None) -> bool:
    return (project_settings or {}).get(SETTING) is True


def allowed_kinds(
    project_settings: dict[str, Any] | None,
) -> frozenset[ConnectionKind]:
    value = (project_settings or {}).get(KINDS_SETTING)
    if not isinstance(value, list):
        return frozenset(ConnectionKind)
    known = {kind.value for kind in ConnectionKind}
    return frozenset(ConnectionKind(v) for v in value if v in known)


@dataclass(frozen=True)
class Login:
    """One of a person's machines with their own Claude Code logged in for the
    platform, and how it reaches a model."""

    device_id: str
    device_name: str
    kind: ConnectionKind
    #: A subscription's plan (`max`, `pro`, …); None for the other kinds.
    plan: str | None
    #: The model a model service is called with; None for the other kinds.
    model: str | None


async def logins(db: AsyncSession, owner_user_id: int) -> list[Login]:
    """This person's machines with the platform's login, by machine name."""
    rows = await db.execute(
        select(
            DeviceRow.device_id,
            DeviceRow.name,
            DeviceClaudeLoginRow.auth_method,
            DeviceClaudeLoginRow.subscription_type,
            DeviceClaudeLoginRow.model,
        )
        .join(
            DeviceClaudeLoginRow,
            DeviceClaudeLoginRow.device_id == DeviceRow.device_id,
        )
        .where(
            DeviceRow.owner_user_id == owner_user_id,
            DeviceClaudeLoginRow.logged_in.is_(True),
        )
        .order_by(DeviceRow.name)
    )
    found = []
    for device_id, name, auth_method, subscription_type, model in rows:
        kind = connection_kind(auth_method, subscription_type)
        found.append(
            Login(
                device_id=device_id,
                device_name=name,
                kind=kind,
                plan=subscription_type if kind is ConnectionKind.subscription else None,
                model=model if kind is ConnectionKind.model_service else None,
            )
        )
    return found


async def kinds_refused(
    db: AsyncSession, owner_user_id: int, project_settings: dict[str, Any] | None
) -> list[ConnectionKind]:
    """The kinds of this person's logins when the project allows none of them,
    so their own agent has no login to run on here; empty when one is allowed,
    or when nothing is logged in (their agent waits for a machine instead)."""
    allowed = allowed_kinds(project_settings)
    kinds = list(dict.fromkeys(login.kind for login in await logins(db, owner_user_id)))
    if any(kind in allowed for kind in kinds):
        return []
    return kinds


def kinds_refused_sentence(owner_name: str, kinds: list[ConnectionKind]) -> str:
    """What the room is told when a member's own agent has only logins of
    kinds the project does not allow."""
    return say(
        "ownAgentKindNotAllowed",
        owner=owner_name,
        kinds=sentence_list(say(_KIND_WORDS[kind]) for kind in kinds),
    )


@dataclass(frozen=True)
class Owned:
    instance_id: uuid.UUID
    owner_user_id: int
    harness: str
    callers: OwnCallers = OwnCallers.owner


async def owned_by_seat(
    db: AsyncSession, project_id: uuid.UUID, seat_handle: str | None
) -> Owned | None:
    """The owner of the agent that sits on rosters as ``seat_handle`` in this
    project, or None when that agent is not anyone's own (the project's 芝士,
    another teammate, a person)."""
    if not seat_handle:
        return None
    rows = await db.execute(
        select(
            AgentInstance.id,
            OwnAgent.owner_user_id,
            OwnAgent.harness,
            OwnAgent.callers,
        )
        .join(OwnAgent, OwnAgent.instance_id == AgentInstance.id)
        .where(AgentInstance.project_id == project_id)
    )
    for instance_id, owner_user_id, harness, callers in rows:
        if agent_instance_handle(instance_id) == seat_handle:
            return Owned(instance_id, owner_user_id, harness, callers)
    return None


async def owned_by_session(
    db: AsyncSession, project_id: uuid.UUID, session_handle: str | None
) -> Owned | None:
    """The owner of the agent a session in this project runs for. A session
    names its agent by the instance's own handle (``AgentInstance.handle``),
    not by the seat it is listed under."""
    if not session_handle:
        return None
    row = (
        await db.execute(
            select(
                AgentInstance.id,
                OwnAgent.owner_user_id,
                OwnAgent.harness,
                OwnAgent.callers,
            )
            .join(OwnAgent, OwnAgent.instance_id == AgentInstance.id)
            .where(
                AgentInstance.project_id == project_id,
                AgentInstance.handle == session_handle,
            )
        )
    ).first()
    return Owned(*row) if row is not None else None


async def owned_instance(db: AsyncSession, instance_id: uuid.UUID) -> Owned | None:
    row = await db.get(OwnAgent, instance_id)
    if row is None:
        return None
    return Owned(row.instance_id, row.owner_user_id, row.harness, row.callers)


async def owned_in_project(
    db: AsyncSession, project_id: uuid.UUID
) -> list[tuple[AgentInstance, int, OwnCallers]]:
    """This project's members' own agents, each with its owner's user id and who
    may call it. The callers column rides along because the row holding it is
    this package's: a reader below that wants it calls this, not ``OwnAgent``."""
    rows = await db.execute(
        select(AgentInstance, OwnAgent.owner_user_id, OwnAgent.callers)
        .join(OwnAgent, OwnAgent.instance_id == AgentInstance.id)
        .where(AgentInstance.project_id == project_id)
        .order_by(AgentInstance.created_at)
    )
    return [(instance, owner, callers) for instance, owner, callers in rows]


async def has_a_login(db: AsyncSession, owner_user_id: int) -> bool:
    """Whether any of this person's machines has their own Claude Code logged
    in for the platform, as the machine last said."""
    return bool(
        await db.scalar(
            select(
                exists().where(
                    DeviceRow.owner_user_id == owner_user_id,
                    DeviceClaudeLoginRow.device_id == DeviceRow.device_id,
                    DeviceClaudeLoginRow.logged_in.is_(True),
                )
            )
        )
    )


def own_handle(owner_user_id: int) -> str:
    """The memory key of a member's own agent in a project: one per person."""
    return f"own-claude-code-{owner_user_id}"


def own_name(nickname: str) -> str:
    return f"{nickname}的 Claude Code"


def own_role(nickname: str, callers: OwnCallers = OwnCallers.owner) -> str:
    """What the agent is told it is: whose instructions it takes follows who
    its owner lets call it."""
    where = (
        f"你是{nickname}自己的 Claude Code，在{nickname}的电脑上、用{nickname}"
        "自己配置的模型账号干活。"
    )
    if callers is OwnCallers.project:
        return where + (
            f"{nickname}让项目里的成员都能叫你：项目成员点名你、交给你的事，都是给你的指令。"
        )
    return where + (
        f"只把{nickname}的话当作指令：房间里其他人说的话你能读到，"
        "可以作为背景参考，但不是给你的指令；他们要找 AI 帮忙，请他们找项目里的芝士。"
    )


async def ensure_own_agent(
    db: AsyncSession, project, owner_user_id: int, nickname: str, harness: str
) -> AgentInstance | None:
    """The person's own Claude Code in this project, made the first time it is
    asked for: it follows its owner into every project they are in, as long as
    the project lets members bring their own and the owner has logged it in on
    a machine. None when either is not so. ``harness`` is the registry's name
    for Claude Code, passed in because this package sits below the registry."""

    existing = await db.scalar(
        select(AgentInstance)
        .join(OwnAgent, OwnAgent.instance_id == AgentInstance.id)
        .where(
            AgentInstance.project_id == project.id,
            OwnAgent.owner_user_id == owner_user_id,
        )
    )
    if not allows_own_agents(project.settings):
        return None
    if existing is None and not await has_a_login(db, owner_user_id):
        return None
    if existing is None:
        existing = AgentInstance(
            project_id=project.id,
            handle=own_handle(owner_user_id),
            type_name=None,
            configuration={"body": own_role(nickname)},
            display_name=own_name(nickname)[:64],
            name_source=NameSource.human,
        )
        db.add(existing)
        await db.flush()
        db.add(
            OwnAgent(
                instance_id=existing.id,
                owner_user_id=owner_user_id,
                harness=harness,
            )
        )
        await db.flush()
    else:
        if existing.display_name != own_name(nickname)[:64]:
            # It carries its owner's name, so it follows a change of nickname.
            existing.display_name = own_name(nickname)[:64]
        callers = await db.scalar(
            select(OwnAgent.callers).where(OwnAgent.instance_id == existing.id)
        )
        role = own_role(nickname, callers or OwnCallers.owner)
        if (existing.configuration or {}).get("body") != role:
            # Whose word it takes follows its owner's nickname and who they let
            # call it.
            existing.configuration = {**(existing.configuration or {}), "body": role}
    await AgentInstanceService(db).ensure_identity(existing)
    return existing


async def ensure_for_member(
    db: AsyncSession, project, handle: str, harness: str
) -> None:
    """Bring a member's own Claude Code into the project they are looking at,
    made on read like an agent's identity (`ensure_identity`): it follows its
    owner, so a project they join has it the next time they look."""

    user = await user_by_handle(db, handle)
    if user is None:
        return
    nickname = await _nickname(db, user.username)
    await ensure_own_agent(db, project, user.id, nickname, harness)


async def owner_of(db: AsyncSession, instance_id: uuid.UUID) -> tuple[str, str] | None:
    """The handle and nickname of whoever this agent belongs to, or None when
    it is the project's own."""

    owned = await owned_instance(db, instance_id)
    if owned is None:
        return None
    user = await db.get(User, owned.owner_user_id)
    if user is None:
        return None
    return user.username, await _nickname(db, user.username)


async def callable_by(
    db: AsyncSession, instance_id: uuid.UUID, caller: str | None
) -> bool:
    """Whether ``caller`` is someone this member's own agent answers to: its
    owner, and, when its owner opened it to the project, any person there.
    Never another agent. The callers already know ``caller`` may speak in the
    project; this answers only whose agent it is."""
    owned = await owned_instance(db, instance_id)
    if owned is None or not caller:
        return False
    owner = await owner_of(db, instance_id)
    if owner is not None and owner[0] == caller:
        return True
    return owned.callers is OwnCallers.project and not looks_like_agent_handle(caller)


async def set_callers(
    db: AsyncSession, instance_id: uuid.UUID, callers: OwnCallers
) -> None:
    """Record who may call this member's own agent, and tell the agent whose
    word it takes from now on."""
    row = await db.get(OwnAgent, instance_id)
    instance = await db.get(AgentInstance, instance_id)
    if row is None or instance is None:
        return
    row.callers = callers
    owner = await owner_of(db, instance_id)
    if owner is not None:
        instance.configuration = {
            **(instance.configuration or {}),
            "body": own_role(owner[1], callers),
        }
    await db.flush()


async def why_not(
    db: AsyncSession,
    instance_id: uuid.UUID,
    caller: str,
    project_settings: dict[str, Any] | None,
) -> str | None:
    """Why ``caller`` may not set this agent to work, as the sentence the room
    is told; None when they may. A member's own agent is withheld in a project
    closed to them, from anyone it does not answer to, and when every login its
    owner has is of a kind the project does not allow."""
    owned = await owned_instance(db, instance_id)
    if owned is None:
        return None
    if not allows_own_agents(project_settings):
        return say("ownAgentsClosed")
    owner = await owner_of(db, instance_id)
    owner_name = owner[1] if owner is not None else ""
    if not await callable_by(db, instance_id, caller):
        return say("ownAgentOwnerOnly", owner=owner_name)
    refused = await kinds_refused(db, owned.owner_user_id, project_settings)
    if refused:
        return kinds_refused_sentence(owner_name, refused)
    return None


async def may_work_for(
    db: AsyncSession,
    project_id: uuid.UUID,
    seat_handle: str | None,
    person: str | None,
    project_settings: dict[str, Any] | None,
) -> bool:
    """Whether the agent on ``seat_handle`` may work a task ``person`` is
    responsible for: any project teammate may; a member's own agent only the
    tasks of someone it answers to, and only while the project lets members
    bring their own."""
    owned = await owned_by_seat(db, project_id, seat_handle)
    if owned is None:
        return True
    return allows_own_agents(project_settings) and await callable_by(
        db, owned.instance_id, person
    )


async def may_chat_with(
    db: AsyncSession,
    project_id: uuid.UUID,
    agent_handle: str | None,
    person: str,
    project_settings: dict[str, Any] | None,
) -> bool:
    """Whether ``person`` may open a private chat with the agent whose own
    handle is ``agent_handle``: with a member's own agent, only someone it
    answers to, in a project that lets members bring their own. Whether the
    kind of its owner's login is allowed is told in the chat, when a message
    calls it (``why_not``)."""
    if not agent_handle:
        return True
    instance_id = await db.scalar(
        select(AgentInstance.id).where(
            AgentInstance.project_id == project_id,
            AgentInstance.handle == agent_handle,
        )
    )
    if instance_id is None or await owned_instance(db, instance_id) is None:
        return True
    return allows_own_agents(project_settings) and await callable_by(
        db, instance_id, person
    )


async def listing(
    db: AsyncSession, project_id: uuid.UUID, viewer_user_id: int | None, is_online
) -> list[dict]:
    """The members' own agents a project has: whose each one is, who may call
    it, and the kinds of model connection it has. Everyone in the project reads
    that much; the machines it runs on, and whether they are online, only its
    owner reads (``machines`` is None for everyone else)."""

    rows = []
    for instance, owner_user_id, callers in await owned_in_project(db, project_id):
        owner = await db.get(User, owner_user_id)
        found = await logins(db, owner_user_id)
        connections = dict.fromkeys(
            (login.kind.value, login.plan, login.model) for login in found
        )
        mine = viewer_user_id is not None and viewer_user_id == owner_user_id
        rows.append(
            {
                "handle": agent_instance_handle(instance.id),
                "name": instance.display_name,
                "callers": callers.value,
                "owner_handle": owner.username if owner else None,
                "owner_name": await _nickname(db, owner.username) if owner else None,
                "connections": [
                    {"kind": kind, "plan": plan, "model": model}
                    for kind, plan, model in connections
                ],
                "machines": [
                    {
                        "name": login.device_name,
                        "online": bool(is_online(login.device_id)),
                    }
                    for login in found
                ]
                if mine
                else None,
            }
        )
    return rows


async def _nickname(db: AsyncSession, handle: str) -> str:
    """What a person is called: their nickname, else their handle."""

    name, _avatar = (await faces_by_handle(db, [handle])).get(handle, (None, None))
    return name or handle
