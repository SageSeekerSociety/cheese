"""Each agent's execution environment in a conversation, and the lease its
session takes on the machine that environment places it on.

`GET /topics/{topic_id}/environment` reads them (with the default a new agent
gets there and the sandbox specs the project is offered),
`PUT /topics/{topic_id}/environment-default` changes that default, and
`PUT /topics/{topic_id}/sessions/{session_id}/environment` changes one agent's.
`POST /topics/{topic_id}/sessions/{session_id}/work-lease` brings a session's
hands up on the machine its environment places it on. ``topic_id`` is a room or
a task: each is a conversation, with its own agents and its own default.

The module was split out of `app/api/routes/topics.py`, which imports nothing
from it. ``ProjectRepository`` is imported from topics.py rather than from
`app.domain.project.repositories` so that the route/repository ledger in
`tests/unit/test_domain_import_guard.py` does not grow.
"""

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep, require_seated_agent
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.topics_deps import ProjectRepository
from app.api.write_access import ROUTE_DECIDES
from app.core import credentials
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    NotFoundError,
)
from app.core.sandbox_auth import held_by_a_machine, scoped_token_claims
from app.core.sentences import say
from app.domain.agent.device_hub import device_hub
from app.domain.agent.environment import Environment, dump, parse, stored
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.machine import holder as holders
from app.domain.machine import session_work as work_lease
from app.domain.machine.environments import (
    check_allowed,
    default_for,
    offered_specs,
)
from app.domain.machine.session_reports import conversation_sessions, devices_held_by
from app.domain.membership.services import MemberService
from app.domain.project_permissions import Permission
from app.domain.room_task.place import place_or_404, session_keeps_work
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics", tags=["topics"])


async def _may_move_task(db, resolver, actor, topic, task) -> bool:
    """Who changes environments in a task: its owner, a manager of the
    project, or the owner of a device the task holds now (taking a task off
    one's own machine); a session only in its own task."""
    if actor.via != "token":
        return resolver.credential_conversation() == task.id
    if actor.handle == task.owner_handle:
        return True
    try:
        await MemberService(db).require(
            topic.project_id, actor, Permission.edit_settings
        )
        return True
    except ForbiddenError:
        pass

    devices = sql_device_service(db)
    held = await devices.get_devices(list(await devices_held_by(db, task.id)))
    return any(
        [await devices.may_manage(device, actor.user_id) for device in held.values()]
    )


async def _place_for_change(db, resolver, topic_id: uuid.UUID):
    """The conversation, its room, its task, project and the acting caller,
    once the caller may change environments there."""
    place = await place_or_404(db, topic_id)
    topic, task = place.room, place.task
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic.id
    )
    if task is not None and not await _may_move_task(db, resolver, actor, topic, task):
        raise ForbiddenError(say("taskComputeOwnerOnly"))
    project = await ProjectRepository(db).get(topic.project_id)
    if project is None:
        raise NotFoundError(say("projectNotFound"))
    return place, topic, task, project, actor


class SandboxSpecOut(BaseModel):
    spec: str
    #: In the reader's language; ``i18n`` carries its key.
    label: str


class EnvironmentDeviceOut(BaseModel):
    device_id: str
    name: str
    online: bool
    #: The reader enrolled it: only they may run an agent on it unisolated.
    owned: bool


class SessionEnvironmentOut(BaseModel):
    session_id: str
    agent_handle: str
    seat_handle: str
    #: None until it first needs one; it then takes the default.
    environment: Environment | None
    status: str | None
    online: bool


class TopicEnvironmentOut(BaseModel):
    #: What this conversation itself says; None when it inherits.
    default: Environment | None
    default_from: Literal["task", "room", "project", "deployment"]
    effective_default: Environment
    sessions: list[SessionEnvironmentOut]
    devices: list[EnvironmentDeviceOut]
    specs: list[SandboxSpecOut]


class EnvironmentDefaultOut(BaseModel):
    default: Environment | None


class SessionEnvironmentChangeOut(BaseModel):
    environment: Environment
    keeps_nothing: bool


@router.get("/{topic_id}/environment", **typed_response(TopicEnvironmentOut))
async def get_environment(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Each agent's environment in this conversation, the default a new agent
    gets here and where it comes from, the sandbox specs this project is
    offered, and the people's computers one may run on unisolated."""
    place = await place_or_404(db, topic_id)
    topic, task = place.room, place.task
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic.id, room=topic
    )
    project = await ProjectRepository(db).get(topic.project_id)
    if project is None:
        raise NotFoundError(say("projectNotFound"))
    chosen = default_for(topic, task, project.settings)
    own_default = stored(
        task.default_environment if task is not None else topic.default_environment
    )
    all_devices = await sql_device_service(db).list_devices_for_project(
        topic.project_id
    )
    devices = [device for device in all_devices if device.supply is Supply.self_hosted]
    return ok(
        {
            # What this conversation itself says; None when it inherits.
            "default": dump(own_default),
            "default_from": chosen.source,
            "effective_default": dump(chosen.environment),
            "sessions": await conversation_sessions(db, project, place),
            "devices": [
                {
                    "device_id": device.device_id,
                    "name": device.name,
                    "online": device_hub.is_online(device.device_id),
                    # Whether the reader may run an agent on it unisolated.
                    "owned": actor.via == "token"
                    and actor.user_id is not None
                    and await sql_device_service(db).may_manage(device, actor.user_id),
                }
                for device in devices
            ],
            "specs": await offered_specs(db, project, project_devices=all_devices),
        }
    )


class EnvironmentDefaultIn(BaseModel):
    environment: dict | None = None


@router.put(
    "/{topic_id}/environment-default",
    dependencies=[ROUTE_DECIDES],
    **typed_response(EnvironmentDefaultOut),
)
async def set_environment_default(
    topic_id: uuid.UUID,
    body: EnvironmentDefaultIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """What a new agent in this conversation gets; None to take the room's
    (for a task) or the project's. Nobody already here moves. A person's
    choice: an agent changes only its own environment."""
    place, topic, task, project, actor = await _place_for_change(db, resolver, topic_id)
    if actor.via != "token":
        raise ForbiddenError(say("environmentDefaultPeopleOnly"))
    environment = None if body.environment is None else parse(body.environment)
    if environment is not None:
        await check_allowed(db, project, environment, actor, task=task)
    target = (
        task
        if task is not None
        else await TopicService(db).lock_for_execution(topic.id)
    )
    target.default_environment = dump(environment)
    await db.commit()
    return ok({"default": dump(environment)})


class SessionEnvironmentIn(BaseModel):
    environment: dict
    #: Leave the session alone while its room is mid-turn (the project's bulk
    #: switch), rather than take its machine away from a turn.
    if_idle: bool = False


@router.put(
    "/{topic_id}/sessions/{session_id}/environment",
    dependencies=[ROUTE_DECIDES],
    **typed_response(SessionEnvironmentChangeOut),
)
async def set_session_environment(
    topic_id: uuid.UUID,
    session_id: uuid.UUID,
    body: SessionEnvironmentIn,
    request: Request,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Give one agent here another environment. Only that agent moves, after
    one best-effort checkpoint where it was. An agent may change its own,
    never another's, and never to a machine without isolation."""
    place, topic, task, project, actor = await _place_for_change(db, resolver, topic_id)
    claims = scoped_token_claims(credentials.presented(request.headers)) or {}
    scoped_session = claims.get("session") if actor.via == "cheese" else None
    if actor.via == "cheese" and (
        scoped_session != str(session_id)
        or claims.get("r") != str(topic.resource_id or topic.id)
    ):
        raise ForbiddenError(say("environmentOwnOnly"))
    row = await AgentSessionService(db).by_id(session_id)
    if row is None or row.conversation_id != place.conversation_id:
        raise NotFoundError("Session not found")
    environment = parse(body.environment)
    await check_allowed(db, project, environment, actor, task=task)
    await work_lease.switch_environment(
        db,
        topic_id=topic.id,
        session_id=session_id,
        actor=actor,
        environment=environment,
        if_idle=body.if_idle,
    )
    return ok(
        {
            "environment": dump(environment),
            # A session whose work is not kept (a 支线, a task not started yet)
            # keeps nothing wherever it moves; the agent is told so with the
            # answer to its own move (`cheese_session(action=environment)`).
            "keeps_nothing": scoped_session is not None
            and not await session_keeps_work(db, scoped_session),
        }
    )


class WorkLeaseRequest(BaseModel):
    env: dict[str, str] = Field(default_factory=dict)
    timeout: float = Field(default=660, gt=0, le=660)
    # The caller hands the answer's `notice` to the agent with its tool's result
    # (`session_work.ensure`).
    tells_agent: bool = False


@router.post(
    "/{topic_id}/sessions/{session_id}/work-lease", dependencies=[ROUTE_DECIDES]
)
async def acquire_session_work_lease(
    topic_id: uuid.UUID,
    session_id: uuid.UUID,
    body: WorkLeaseRequest,
    request: Request,
    db: DbSession,
) -> dict:

    token = credentials.presented(request.headers)
    claims = scoped_token_claims(token)
    if claims is not None and not held_by_a_machine(claims):
        place = await place_or_404(db, topic_id)
        await require_seated_agent(
            db, token, project_id=place.project_id, topic_id=topic_id
        )
        holder_id = place.room_id
    else:
        # A person's own 芝士 asks with its own credential, for a sandbox of
        # the conversation's own (``machine.holder.Personal``).
        claims = await holders.personal_claims(db, token, topic_id, session_id)
        holder_id = topic_id
    if claims is None:
        raise AuthenticationRequiredError("A session execution credential is required")
    try:
        # body.timeout caps how long the caller waits for a machine being
        # prepared (wait_s), and the whole call is held to it. A session that
        # will not wait asks with ~0, which still gets one attempt's time
        # (ANSWER_S, never more than a waiting call is held): bounded by ~0
        # itself, a ready machine answered 504.
        floor = min(work_lease.ANSWER_S, work_lease.PREPARING_WAIT_S)
        async with asyncio.timeout(max(body.timeout, floor)):
            return ok(
                await work_lease.ensure(
                    db,
                    topic_id=holder_id,
                    session_id=session_id,
                    claims=claims,
                    token=token,
                    env=body.env,
                    wait_s=min(work_lease.PREPARING_WAIT_S, body.timeout),
                    gone=request.is_disconnected,
                    tells_agent=body.tells_agent,
                )
            )
    except TimeoutError:
        # One step of preparing the machine outlasted this request. That is
        # still a machine being prepared, so it answers as one: the session's
        # client asks again until its own deadline (`executor_transport.acquire`).
        # A 504 here reached the agent as a failed tool call it read as final,
        # and it slept instead of letting the next call wait.
        # The step it outlasted may have been a query — waiting on the room's
        # lock while a switch pushes, say — and a query cancelled mid-flight
        # leaves the session unusable: the commit after this answer would
        # raise and turn it into a 500. Nothing of this attempt is kept.
        await db.rollback()
        return ok({"unavailable": str(say("workComputerPreparing")), "preparing": True})
