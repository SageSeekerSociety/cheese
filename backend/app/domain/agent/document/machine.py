"""The task's machine, for a reader: a document's 芝士 looking at the task's
work while it answers a question about the task's document.

It is the machine the task's sessions already hold, never one taken for the
reader. Taking hands (`session_work.ensure`) places a cloud sandbox or claims a
device; a question in a comment is not worth either, so a task with no machine
in hand right now (none chosen yet, a cloud one released, a device offline)
lends none, and the question is answered without it.

What is lent is the session's own lease, as its executor knows it, with a
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
    """The execution target of the machine a conversation's sessions hold, to
    read through for ``ttl_s``; None when they hold none that is there now. A
    conversation's sessions run on its channel's machine, in the channel's
    current generation; the seat's own session comes first."""
    topic = await db.get(Topic, await room_of(db, conversation_id))
    if topic is None:
        return None
    resource = str(topic.resource_id or topic.id)
    rows = list(
        await db.scalars(
            select(AgentSession).where(AgentSession.conversation_id == conversation_id)
        )
    )
    rows.sort(key=lambda row: row.agent_handle != seat)
    for row in rows:
        lease = row.work_lease or {}
        if (
            lease.get("kind") != "device"
            or lease.get("status", "ready") != "ready"
            or not all(lease.get(k) for k in ("url", "workspace", "generation"))
            or (row.runtime_location or {}).get("resource_id") != resource
            or not device_hub.is_online(str(lease.get("device_id") or ""))
        ):
            continue
        token = bind_resource_token(
            mint_scoped_token(
                project_id=str(project_id),
                topic_id=str(conversation_id),
                agent_handle=seat,
                ttl_s=ttl_s,
            ),
            resource,
            session_id=str(row.id),
            lease_generation=str(lease["generation"]),
            reading=True,
        )
        return {
            **{key: lease[key] for key in _TARGET if key in lease},
            "execution_token": token,
        }
    return None
