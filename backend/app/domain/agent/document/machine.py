"""The task's machine, for a reader: a document's 芝士 looking at the task's
work while it answers a question about the task's document.

It is the environment the task points to, as it already is, never one taken
for the reader. Taking hands (`session_work.ensure`) places a cloud sandbox or claims a
device; a question in a comment is not worth either, so a task with no machine
in hand right now (none chosen yet, a cloud one released, a device offline)
lends none, and the question is answered without it.

What is lent is that environment, as its executor knows it, with a
credential that only reads (`bind_resource_token` ``reading``): the task's
checkout as its agent left it, uncommitted changes and all, and nothing the
reader could change in it.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent.device_hub import device_hub
from app.domain.agent_session.models import AgentSession
from app.domain.conversation.services import room_of
from app.domain.environment import services as environment_rows
from app.domain.topic.models import Topic

#: What of a lease the reader's executor client needs (`RemoteClient`): a
#: lease being re-checked mid-turn carries a claim besides, and a reader
#: started then would otherwise be started with a target of its own.
_TARGET = ("kind", "device_id", "generation", "url", "workspace", "mcp_servers")


async def machine_to_read(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    seat: str,
    ttl_s: int,
) -> dict | None:
    """The execution target of the environment the conversation points to,
    to read through for ``ttl_s``; None when it points to none that is there
    now: ready, on its channel's current generation, its machine online.

    The credential acts through a session attached to it, since the
    execution route admits a call only as one (its ``session`` and ``lease``
    claims): the seat's own when it is attached there, otherwise the one
    that acted there last. Nothing it reads depends on which."""
    topic = await db.get(Topic, await room_of(db, conversation_id))
    if topic is None:
        return None
    resource = str(topic.resource_id or topic.id)
    environment = await environment_rows.environment_of_conversation(
        db, conversation_id
    )
    if environment is None or environment.room_resource_id != resource:
        return None
    lease = await environment_rows.view(db, environment.id) or {}
    if (
        lease.get("status", "ready") != "ready"
        or not all(lease.get(k) for k in ("url", "workspace"))
        or not device_hub.is_online(str(lease.get("device_id") or ""))
    ):
        return None
    attached = await environment_rows.attachments_of(db, environment.id)
    if not attached:
        return None
    rows = await db.execute(
        select(AgentSession.id, AgentSession.agent_handle).where(
            AgentSession.id.in_([one.session_id for one in attached])
        )
    )
    handles = {session_id: handle for session_id, handle in rows.all()}
    acting = sorted(attached, key=lambda one: handles.get(one.session_id) != seat)[0]
    token = bind_resource_token(
        mint_scoped_token(
            project_id=str(project_id),
            topic_id=str(conversation_id),
            agent_handle=seat,
            ttl_s=ttl_s,
        ),
        resource,
        session_id=str(acting.session_id),
        lease_generation=acting.generation,
        reading=True,
    )
    lease = {**lease, "generation": acting.generation}
    return {
        **{key: lease[key] for key in _TARGET if key in lease},
        "execution_token": token,
    }
