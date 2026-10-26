"""Which session a sandbox's request belongs to.

A sandbox holds no credential: the process that owns the device links names
the device and the state directory its request came in on
(``device_hub.machine``), and the caller is the session whose current lease is
that state on that device. It is looked up on every request, so a session that
ended, moved or was given another lease is nobody from its next request on.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.place import ROOT_PLACEHOLDER, footprint_root
from app.domain.agent_session.models import AgentSession
from app.domain.conversation.models import Conversation
from app.domain.machine.session_work import session_author
from app.domain.project.services import ProjectService


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
