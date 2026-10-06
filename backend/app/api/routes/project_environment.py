"""Project environment settings and explicit room preparation controls."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_chat_service
from app.api.response import ok
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.db import get_db
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import notice_keys, say
from app.domain.agent.chat import ChatService
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_provider import environment_status
from app.domain.agent.environment_failures import open_failures, waiting_on
from app.domain.agent.models import AgentTurn
from app.domain.agent.platform_notices import (
    EVENT_ENVIRONMENT_REPAIRED,
    SEVERITY_INFO,
    WHO_CHEESE,
    notice,
)
from app.domain.agent.runtime import addressed_to_agent
from app.domain.conversation.services import of_room
from app.domain.device.wiring import sql_device_service
from app.domain.machine.models import MachineStatus
from app.domain.machine.repositories import CloudHostRepository
from app.domain.membership.roster import roster
from app.domain.membership.services import MemberService
from app.domain.project.environment import EnvironmentConfig, project_environment
from app.domain.project.models import Project
from app.domain.project.services import refuse_writes_if_archived
from app.domain.topic.models import Topic, TopicKind, TopicStatus
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.repositories import UserRepository

router = APIRouter(prefix="/projects/{project_id}/environment", tags=["environment"])
Db = Annotated[AsyncSession, Depends(get_db)]
User = Annotated[AuthUserInfo, Depends(require_auth_user)]
Chat = Annotated[ChatService, Depends(get_chat_service)]


async def access(
    db: AsyncSession,
    project_id: uuid.UUID,
    auth_user: AuthUserInfo,
    *,
    write: bool = False,
) -> tuple[Project, bool]:
    project = await db.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    user = await UserRepository(db).get_by_id(auth_user.user_id)
    handle = user.username if user else ""
    steward = bool(user) and await MemberService(db).manages(project_id, handle)
    member = bool(user) and any(
        m.handle == handle for m in await roster(db, project_id)
    )
    if not steward and (write or not member):
        raise ForbiddenError(say("environmentAccessForbidden"))
    # This router authenticates on its own rather than through ActorResolver, so
    # it asks the archived-project question that the resolver asks for the rest.
    if write:
        await refuse_writes_if_archived(db, project_id)
    return project, steward


async def room(
    db: AsyncSession,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    *,
    lock: bool = False,
    viewer: AuthUserInfo | None = None,
) -> Topic:
    """A channel of the project. Asked for a person (``viewer``), a private
    channel they are not in is not found, as it is everywhere else."""
    statement = select(Topic).where(
        Topic.id == topic_id,
        Topic.project_id == project_id,
        Topic.is_private.is_(False),
    )
    if lock:
        # The session keeps objects across commit, so a locked re-read must
        # load the row again rather than hand back the unlocked read's copy.
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    topic = await db.scalar(statement)
    if topic is None or (
        viewer is not None
        and not await TopicMemberService(db).seen([topic], await _handle(db, viewer))
    ):
        raise NotFoundError("Room not found")
    return topic


async def _handle(db: AsyncSession, auth_user: AuthUserInfo) -> str:
    user = await UserRepository(db).get_by_id(auth_user.user_id)
    return user.username if user else ""


async def require_idle(db: AsyncSession, topic: Topic) -> None:
    if topic.archived_at is not None:
        raise ValidationError(say("unarchiveBeforeEnvironmentChange"))
    active = await db.scalar(
        select(AgentTurn.id)
        .where(
            of_room(AgentTurn.conversation_id, topic.id),
            AgentTurn.stopped_at.is_(None),
        )
        .limit(1)
    )
    if active is not None:
        raise ValidationError(say("roomBusyEnvironmentChange"))


@router.get("")
async def get_environment(project_id: uuid.UUID, db: Db, user: User) -> dict:
    project, can_edit = await access(db, project_id, user)
    topics = (
        await db.scalars(
            select(Topic)
            .where(
                Topic.project_id == project_id,
                Topic.is_private.is_(False),
                Topic.kind != TopicKind.root,
                Topic.status != TopicStatus.archived,
            )
            .order_by(Topic.created_at)
        )
    ).all()
    # A private channel is listed to its people only.
    topics = await TopicMemberService(db).seen(list(topics), await _handle(db, user))
    failures = await open_failures(db, [t.id for t in topics])
    return ok(
        {
            "config": project_environment(project.settings),
            "can_edit": can_edit,
            "rooms": [
                {
                    "id": str(t.id),
                    "title": t.title,
                    "revision": (t.environment or {}).get("revision"),
                    # The latest failure nobody has retried: when, what, the
                    # end of its log, how many conversations wait on it.
                    **({"failure": failures[t.id]} if t.id in failures else {}),
                }
                for t in topics
            ],
        }
    )


@router.put("")
async def save_environment(
    project_id: uuid.UUID, body: EnvironmentConfig, db: Db, user: User
) -> dict:
    await access(db, project_id, user, write=True)
    project = await db.scalar(
        select(Project).where(Project.id == project_id).with_for_update(key_share=True)
    )
    config = body.snapshot()
    if project is None:
        raise NotFoundError("Project not found")
    project.settings = {**(project.settings or {}), "environment": config}
    await db.flush()
    return ok(config)


@router.get("/rooms/{topic_id}")
async def get_room_environment(
    project_id: uuid.UUID, topic_id: uuid.UUID, db: Db, user: User
) -> dict:
    await access(db, project_id, user)
    topic = await room(db, project_id, topic_id, viewer=user)
    binding = await sql_device_service(db).topic_binding(topic_id)
    if binding is None:
        hosts = [
            host
            for _home, host in await CloudHostRepository(db).room_homes(
                topic_id, str(topic.resource_id or topic.id)
            )
        ]
        if not hosts:
            # No sandbox is placed until a session requests execution. A chat
            # message alone is not a pending placement.
            state = {"state": "unbound"}
        elif any(host.status == MachineStatus.error for host in hosts):
            log = say("envSandboxFailed")
            state = {"state": "failed", "log": log, **notice_keys(log=log)}
        else:
            # A sandbox on its way IS preparation, whatever stage it's at.
            state = {"state": "pending"}
    elif not device_hub.is_online(binding.device_id):
        state = {"state": "offline"}
    else:
        state = await environment_status(
            device_hub, binding.device_id, project_id, topic.resource_id or topic_id
        )
    busy = await db.scalar(
        select(AgentTurn.id)
        .where(
            of_room(AgentTurn.conversation_id, topic_id),
            AgentTurn.stopped_at.is_(None),
        )
        .limit(1)
    )
    return ok(
        {
            **state,
            "pinned_revision": (topic.environment or {}).get("revision"),
            **({"busy": True} if busy is not None else {}),
        }
    )


class ApplyEnvironment(BaseModel):
    latest: bool = False


async def reset_idle_room(db: AsyncSession, topic_id: uuid.UUID, project_id: uuid.UUID):
    """Stop the room's old environment on its machine.

    The device calls run with no transaction open. They take up to 10 s, plus
    30 s a screen, and a room row locked across them queues every other
    writer of the room behind them with a pool connection each (dev outage of
    2026-09-18). New prompts in this process are already held off by the
    caller's `chat.edit_environment`; the caller locks the row again after
    this returns and checks the room is still idle before writing.
    """
    topic = await room(db, project_id, topic_id)
    await require_idle(db, topic)
    binding = await sql_device_service(db).topic_binding(topic_id)
    resource_id = topic.resource_id or topic_id
    await db.commit()
    if binding is None:
        return
    if not device_hub.is_online(binding.device_id):
        raise ValidationError(say("machineOfflineCannotConfirmStop"))
    await environment_status(
        device_hub, binding.device_id, project_id, resource_id, action="reset"
    )
    # Closing each screen also releases its runtime subscription.
    for screen in device_hub.screens_for_topic(topic_id):
        await device_hub.close_screen(screen.device_id, screen.sid)


@router.post("/rooms/{topic_id}/apply")
async def apply_environment(
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    body: ApplyEnvironment,
    db: Db,
    user: User,
    chat: Chat,
) -> dict:
    from app.api.deps import get_work_runner

    project, _ = await access(db, project_id, user, write=True)
    async with chat.edit_environment(topic_id):
        topic = await room(db, project_id, topic_id, viewer=user)
        if topic.kind == TopicKind.root:
            raise ValidationError(say("overviewUsesBaseEnvironment"))
        failed = (await open_failures(db, [topic_id])).get(topic_id)
        await reset_idle_room(db, topic_id, project_id)
        topic = await room(db, project_id, topic_id, lock=True)
        await require_idle(db, topic)
        if body.latest or topic.environment is None:
            topic.environment = project_environment(project.settings)
        # Retried: back to where people were told it failed, so the teammate
        # takes up what it could not do (`environment_failures`).
        waiting = await waiting_on(db, topic_id, failed["attempt"]) if failed else []
        # Commit while holding the prompt lock, before another turn can start.
        await db.commit()
        seat = await TopicMemberService(db).addressable_agent_handle(topic_id)
        for conversation in waiting:
            get_work_runner().submit(
                chat,
                conversation,
                author="system",
                content="环境已经重新准备，接着处理此前因为环境没准备好而没做完的事。",
                addressed=addressed_to_agent(seat),
                # Seen as a platform line, not a chat message signed system:
                # the text above is the prompt, for the agent only.
                nudge_event=say("environmentRepaired"),
                nudge_meta=notice(
                    EVENT_ENVIRONMENT_REPAIRED,
                    severity=SEVERITY_INFO,
                    who=WHO_CHEESE,
                ),
            )
    return ok(
        {
            "state": "pending",
            "revision": topic.environment["revision"],
            "resumed": len(waiting),
        }
    )


@router.post("/rooms/{topic_id}/diagnose")
async def diagnose_environment(
    project_id: uuid.UUID, topic_id: uuid.UUID, db: Db, user: User
) -> dict:
    """「让芝士看看」: why this channel's latest failure happened and what to
    change, read from its log and the scripts it ran. Nothing is changed:
    the answer is a proposal a person takes or leaves."""
    from dataclasses import asdict

    from app.domain.project.environment_diagnosis import diagnose

    project, _ = await access(db, project_id, user, write=True)
    topic = await room(db, project_id, topic_id, viewer=user)
    failure = (await open_failures(db, [topic_id])).get(topic_id)
    if failure is None:
        raise ValidationError(say("envNoFailure"))
    ran = topic.environment or project_environment(project.settings)
    answer = await diagnose(db, config=ran, failure=failure)
    await db.commit()
    if answer is None:
        raise ValidationError(say("envDiagnoseFailed"))
    return ok(
        {
            **asdict(answer),
            "ran": {
                "setup_script": ran.get("setup_script") or "",
                "startup_script": ran.get("startup_script") or "",
            },
        }
    )
