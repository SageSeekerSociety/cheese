"""Resolve live attribution and format events before durable output writes."""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.agent.queries import _agent_handle
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent.turn.state.live import HookWorkState, LiveWork
from app.domain.agent.turn.store.events import persist_action, persist_room_event
from app.domain.event_author import AuthorType

_ACTION_LABEL = {"notify": "actionNotify"}


async def announce_action(
    sessions: async_sessionmaker, state: HookWorkState, resource: str
) -> None:
    """Publish a turn's room action only after its idempotent write commits."""
    payload = await persist_action(
        sessions,
        project_id=state.project_id,
        topic_id=state.topic_id,
        turn_id=state.work_id,
        author=state.acting_agent,
        resource=resource,
        label=_ACTION_LABEL[resource],
    )
    if payload is not None:
        await get_broker().publish(
            str(state.topic_id), {"type": "event_block", "block": payload}
        )


async def _persist_room_event(
    sessions: async_sessionmaker,
    live: LiveWork,
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    content: str,
    meta: dict,
    turn_id: uuid.UUID | None,
    eid: str | None = None,
    platform_unsolicited: bool = False,
    author_type: AuthorType = AuthorType.participant,
    inner_id: uuid.UUID | None = None,
    author: str | None = None,
    at: datetime | None = None,
) -> dict | None:
    state = (
        live.hook_work.get((inner_id or topic_id, turn_id))
        if turn_id is not None
        else None
    )
    return await persist_room_event(
        sessions,
        resolve_author=_agent_handle,
        acting_agent=state.acting_agent if state is not None else None,
        project_id=project_id,
        topic_id=topic_id,
        content=content,
        meta=meta,
        turn_id=turn_id,
        eid=eid,
        platform_unsolicited=platform_unsolicited,
        author_type=author_type,
        inner_id=inner_id,
        author=author,
        at=at,
    )
