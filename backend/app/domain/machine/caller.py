"""Which session a sandbox's request belongs to.

A sandbox holds no credential: the process that owns the device links names
the device and the state directory its request came in on
(``device_hub.machine``), and the caller is the session whose current lease is
that state on that device. It is looked up on every request, so a session that
ended, moved or was given another lease is nobody from its next request on.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sandbox_auth import bind_resource_token, for_a_machine, mint_scoped_token
from app.domain.agent.place import ROOT_PLACEHOLDER, footprint_root
from app.domain.agent_session.models import AgentSession
from app.domain.conversation.models import Conversation
from app.domain.conversation.services import room_of
from app.domain.machine.session_work import session_author
from app.domain.project.services import ProjectService
from app.domain.room_task.place import session_keeps_work
from app.domain.topic.services import TopicService

#: How long the credential a socket request is served under lives: it is made
#: for that one request and never leaves the backend.
CALL_CREDENTIAL_TTL_S = 300
#: How long the credential a sandbox is handed for what cannot go down its
#: socket (git's own requests, the preview tunnel, a body over the socket's
#: limit) lives: it is asked for right before that use.
HANDED_CREDENTIAL_TTL_S = 60


@dataclass(frozen=True)
class MachineCaller:
    """The session a sandbox's request belongs to."""

    session_id: str
    project_id: str
    conversation_id: str
    #: The seat the session acts under, as its own launch credential names it
    #: (`session_work.session_author`): its teammate's handle, not the key the
    #: session row is stored under.
    agent_handle: str


def _names(state: str, tag: str) -> bool:
    # The connector is told a state as the backend spelled it for that
    # connector (`HubDevice.spelled`): the root placeholder expanded for one
    # that predates enrollment roots.
    return tag in (state, state.replace(ROOT_PLACEHOLDER, footprint_root()))


async def caller_of(db: AsyncSession, device: str, state: str) -> MachineCaller | None:
    """The session holding ``state`` on ``device`` now, or None."""
    rows = await db.execute(
        select(AgentSession, Conversation.project_id)
        .join(Conversation, Conversation.id == AgentSession.conversation_id)
        .where(AgentSession.work_lease["device_id"].as_string() == device)
    )
    held = next(
        (
            (session, project_id)
            for session, project_id in rows
            if (session.work_lease or {}).get("status", "ready") == "ready"
            and (session.work_lease or {}).get("state")
            and _names(session.work_lease["state"], state)
        ),
        None,
    )
    if held is None:
        return None
    session, project_id = held
    project = await ProjectService(db).get(project_id)
    if project is None:
        return None
    return MachineCaller(
        session_id=str(session.id),
        project_id=str(project_id),
        conversation_id=str(session.conversation_id),
        agent_handle=await session_author(db, project, session.agent_handle),
    )


async def credential_of(
    db: AsyncSession,
    caller: MachineCaller,
    *,
    ttl_s: int = CALL_CREDENTIAL_TTL_S,
    held: bool = True,
) -> str | None:
    """The credential the caller's executor holds, made again for one request:
    the session's launch credential bound to its lease (as
    `session_work.restart_executor` makes it), a 支线's marked as keeping no
    work, and marked as held by a machine (`held=True`) unless kept in-process
    for `/machine/api` (`held=False`). None when the session is gone."""
    row = await db.get(AgentSession, uuid.UUID(caller.session_id))
    if row is None:
        return None
    lease = row.work_lease or {}
    room = await TopicService(db).get_or_404(await room_of(db, row.conversation_id))
    resource = lease.get("room_resource_id") or str(room.resource_id or room.id)
    launch = mint_scoped_token(
        project_id=caller.project_id,
        topic_id=caller.conversation_id,
        agent_handle=caller.agent_handle,
        access_scope="project",
        ttl_s=ttl_s,
    )
    bound = bind_resource_token(
        launch,
        resource,
        session_id=caller.session_id,
        lease_generation=lease.get("generation"),
        scratch=not await session_keeps_work(db, row.id),
    )
    return for_a_machine(bound) if held else bound
