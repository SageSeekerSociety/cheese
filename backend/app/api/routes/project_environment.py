"""Project environment settings and explicit room preparation controls."""

import uuid
from dataclasses import asdict
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_chat_service
from app.api.request_deps import AuthUser, DbSession
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.auth.core import AuthUserInfo
from app.core.errors import ForbiddenError, ValidationError
from app.core.sentences import notice_keys, say
from app.domain.agent.chat import ChatService
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_provider import environment_status
from app.domain.agent.environment import room_is_busy
from app.domain.agent.environment_failures import open_failures, waiting_on
from app.domain.agent.runtime import addressed_to_agent
from app.domain.agent_instance.adapters import project_team
from app.domain.machine.services import HostPool, host_failed
from app.domain.machine.session_reports import leases_on_peoples_devices
from app.domain.membership.roster import roster
from app.domain.membership.services import MemberService
from app.domain.notice_meta import (
    EVENT_ENVIRONMENT_REPAIRED,
    SEVERITY_INFO,
    WHO_CHEESE,
    notice,
)
from app.domain.project.environment import (
    EnvironmentConfig,
    EnvironmentProject,
    EnvironmentRoom,
    load_environment_project,
    load_environment_room,
    project_environment,
    rooms_with_environment,
    save_project_environment,
    set_room_environment,
)
from app.domain.project.services import refuse_writes_if_archived
from app.domain.project_permissions import Permission
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.repositories import UserRepository

router = APIRouter(prefix="/projects/{project_id}/environment", tags=["environment"])


Chat = Annotated[ChatService, Depends(get_chat_service)]


async def access(
    db: AsyncSession,
    project_id: uuid.UUID,
    auth_user: AuthUserInfo,
    *,
    write: bool = False,
) -> tuple[EnvironmentProject, bool]:
    project = await load_environment_project(db, project_id)
    user = await UserRepository(db).get_by_id(auth_user.user_id)
    handle = user.username if user else ""
    steward = bool(user) and await MemberService(db).manages(
        project_id, handle, Permission.edit_settings
    )
    member = bool(user) and any(
        m.handle == handle for m in await roster(db, project_id, team=project_team)
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
) -> EnvironmentRoom:
    """A channel of the project; a private channel stays hidden from outsiders."""
    visible = (
        partial(
            TopicMemberService(db).seen,
            handle=await _handle(db, viewer),
            team=project_team,
        )
        if viewer is not None
        else None
    )
    return await load_environment_room(
        db, project_id, topic_id, lock=lock, visible=visible
    )


async def _handle(db: AsyncSession, auth_user: AuthUserInfo) -> str:
    user = await UserRepository(db).get_by_id(auth_user.user_id)
    return user.username if user else ""


async def require_idle(db: AsyncSession, topic: EnvironmentRoom) -> None:
    if topic.archived_at is not None:
        raise ValidationError(say("unarchiveBeforeEnvironmentChange"))
    if await room_is_busy(db, topic.id):
        raise ValidationError(say("roomBusyEnvironmentChange"))


@router.get("")
async def get_environment(project_id: uuid.UUID, db: DbSession, user: AuthUser) -> dict:
    project, can_edit = await access(db, project_id, user)
    topics = await rooms_with_environment(
        db,
        project_id,
        visible=partial(
            TopicMemberService(db).seen,
            handle=await _handle(db, user),
            team=project_team,
        ),
    )
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


@router.put("", dependencies=[ROUTE_DECIDES])
async def save_environment(
    project_id: uuid.UUID, body: EnvironmentConfig, db: DbSession, user: AuthUser
) -> dict:
    await access(db, project_id, user, write=True)
    config = body.snapshot()
    await save_project_environment(db, project_id, config)
    return ok(config)


@router.get("/rooms/{topic_id}")
async def get_room_environment(
    project_id: uuid.UUID, topic_id: uuid.UUID, db: DbSession, user: AuthUser
) -> dict:
    await access(db, project_id, user)
    topic = await room(db, project_id, topic_id, viewer=user)
    # The room's sessions each have an environment of their own: one on a
    # person's computer reports that computer's environment, and the room's
    # sandboxes on the platform's machines are reported together.
    leases = await leases_on_peoples_devices(db, topic_id)
    lease = leases[0] if leases else None
    if lease is None:
        hosts = await HostPool(db).room_hosts(
            topic_id, str(topic.resource_id or topic.id)
        )
        if not hosts:
            # No sandbox is placed until a session requests execution. A chat
            # message alone is not a pending placement.
            state = {"state": "unbound"}
        elif any(host_failed(host) for host in hosts):
            log = say("envSandboxFailed")
            state = {"state": "failed", "log": log, **notice_keys(log=log)}
        else:
            # A sandbox on its way IS preparation, whatever stage it's at.
            state = {"state": "pending"}
    elif not device_hub.is_online(lease["device_id"]):
        state = {"state": "offline"}
    else:
        state = await environment_status(
            device_hub, lease["device_id"], project_id, uuid.UUID(lease["resource_id"])
        )
    busy = await room_is_busy(db, topic_id)
    return ok(
        {
            **state,
            "pinned_revision": (topic.environment or {}).get("revision"),
            **({"busy": True} if busy else {}),
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
    leases = await leases_on_peoples_devices(db, topic_id)
    await db.commit()
    if any(not device_hub.is_online(lease["device_id"]) for lease in leases):
        raise ValidationError(say("machineOfflineCannotConfirmStop"))
    for lease in leases:
        await environment_status(
            device_hub,
            lease["device_id"],
            project_id,
            uuid.UUID(lease["resource_id"]),
            action="reset",
        )
    # Closing each screen also releases its runtime subscription.
    for screen in device_hub.screens_for_topic(topic_id):
        await device_hub.close_screen(screen.device_id, screen.sid)


@router.post("/rooms/{topic_id}/apply", dependencies=[ROUTE_DECIDES])
async def apply_environment(
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    body: ApplyEnvironment,
    db: DbSession,
    user: AuthUser,
    chat: Chat,
) -> dict:
    # deferred-import: tests patch this name on app.api.deps
    from app.api.deps import get_work_runner

    project, _ = await access(db, project_id, user, write=True)
    async with chat.edit_environment(topic_id):
        topic = await room(db, project_id, topic_id, viewer=user)
        if topic.is_overview:
            raise ValidationError(say("overviewUsesBaseEnvironment"))
        failed = (await open_failures(db, [topic_id])).get(topic_id)
        await reset_idle_room(db, topic_id, project_id)
        topic = await room(db, project_id, topic_id, lock=True)
        await require_idle(db, topic)
        environment = topic.environment
        if body.latest or environment is None:
            environment = project_environment(project.settings)
            await set_room_environment(db, topic_id, environment)
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
            "revision": environment["revision"],
            "resumed": len(waiting),
        }
    )


@router.post("/rooms/{topic_id}/diagnose", dependencies=[ROUTE_DECIDES])
async def diagnose_environment(
    project_id: uuid.UUID, topic_id: uuid.UUID, db: DbSession, user: AuthUser
) -> dict:
    """「让芝士看看」: why this channel's latest failure happened and what to
    change, read from its log and the scripts it ran. Nothing is changed:
    the answer is a proposal a person takes or leaves."""
    # deferred-import: tests patch diagnose on app.domain.environment_diagnosis
    from app.domain.environment_diagnosis import diagnose

    project, _ = await access(db, project_id, user, write=True)
    topic = await room(db, project_id, topic_id, viewer=user)
    failure = (await open_failures(db, [topic_id])).get(topic_id)
    if failure is None:
        raise ValidationError(say("envNoFailure"))
    ran = topic.environment or project_environment(project.settings)
    answer = await diagnose(
        db, project_id=project.id, topic_id=topic_id, config=ran, failure=failure
    )
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
