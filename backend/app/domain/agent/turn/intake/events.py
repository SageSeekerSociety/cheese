"""Resolve live attribution and format events before durable output writes."""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.agent.event_lines import (
    _change_summary_meta,
    _Changeset,
    _format_change_summary,
    _format_tool_event,
    _subagent_event_text,
    _subagent_result_meta,
    _tool_event_meta,
)
from app.domain.agent.queries import _agent_handle
from app.domain.agent.service import AgentToolResult
from app.domain.agent.tool_preview import tool_detail, tool_preview, work_subpath
from app.domain.agent.turn.state.live import LiveWork
from app.domain.agent.turn.store.events import persist_room_event
from app.domain.block.models import AuthorType


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
    in_room: bool = False,
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
        in_room=in_room,
        author_type=author_type,
        inner_id=inner_id,
        author=author,
        at=at,
    )


async def _persist_tool_event(
    sessions: async_sessionmaker,
    live: LiveWork,
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    name: str,
    tool_input: dict,
    platform: bool,
    turn_id: uuid.UUID | None,
    eid: str | None = None,
    platform_unsolicited: bool = False,
    inner_id: uuid.UUID | None = None,
    author: str | None = None,
    at: datetime | None = None,
) -> dict | None:
    """Persist ONE 施工现场 event the moment it streams in, not batched to the
    turn-end tx2. Mirrors _persist_assistant_message's commit-now contract so
    a mid-turn restart/crash never loses the 现场 timeline already produced.
    ``eid`` (the harness's own id for the event) is stamped into meta so a
    record read twice lands once. Returns the persisted block payload so the
    caller can broadcast it as a WS frame."""
    preview = tool_preview(
        name, tool_input, work_dir=work_subpath(project_id, topic_id)
    )
    return await _persist_room_event(
        sessions,
        live,
        project_id=project_id,
        topic_id=topic_id,
        content=_format_tool_event(name, preview),
        meta=_tool_event_meta(
            name,
            preview,
            platform=platform,
            detail=tool_detail(name, tool_input, preview),
        ),
        turn_id=turn_id,
        eid=eid,
        platform_unsolicited=platform_unsolicited,
        inner_id=inner_id,
        author=author,
        at=at,
    )


async def _persist_subagent_result(
    sessions: async_sessionmaker,
    live: LiveWork,
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    event: AgentToolResult,
    turn_id: uuid.UUID | None,
    eid: str | None = None,
    platform_unsolicited: bool = False,
    inner_id: uuid.UUID | None = None,
) -> dict | None:
    """Land a returning subagent's conclusion in the room timeline."""
    return await _persist_room_event(
        sessions,
        live,
        project_id=project_id,
        topic_id=topic_id,
        content=_subagent_event_text(event.description, event.text),
        meta=_subagent_result_meta(event.name, event.description, event.text),
        turn_id=turn_id,
        eid=eid or event.eid,
        platform_unsolicited=platform_unsolicited,
        inner_id=inner_id,
        author=event.agent_handle,
    )


async def _persist_change_summary(
    sessions: async_sessionmaker,
    live: LiveWork,
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    turn_id: uuid.UUID | None,
    changeset: _Changeset,
) -> dict | None:
    """Land 「这一轮改了 N 个文件」 in the room timeline.

    This one goes in the ROOM, not just 现场 (spec §8.5 变更提醒). What 芝士
    changed is the one thing about a turn that is nowhere else in the
    conversation: the doc panel lights up on its own and the accept card
    speaks for itself, but "this turn touched these files" was only ever a
    grey line in a drawer nobody has open."""
    return await _persist_room_event(
        sessions,
        live,
        project_id=project_id,
        topic_id=topic_id,
        content=_format_change_summary(changeset.files),
        meta=_change_summary_meta(changeset),
        turn_id=turn_id,
        in_room=True,
        author_type=AuthorType.platform,  # 平台自己数出来的，不是芝士说的
    )
