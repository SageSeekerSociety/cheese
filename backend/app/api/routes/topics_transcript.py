"""The 现场 timeline is a read projection of immutable harness records."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, page
from app.api.topics_deps import _actor_in_place
from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.agent.run_records import for_site
from app.domain.agent.turn_times import turn_starts
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle
from app.domain.room_task.place import place_or_404
from app.domain.session_record import service
from app.domain.session_record.history import earlier_turns
from app.domain.session_record.views import as_block

router = APIRouter(prefix="/topics", tags=["topics"])


@router.get("/{topic_id}/transcript")
async def topic_transcript(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    limit: int = Query(120, ge=1, le=200),
    before: str | None = Query(None, max_length=512),
    author: str | None = Query(None, min_length=1, max_length=120),
) -> JSONResponse:
    place = await place_or_404(db, topic_id)
    if place.room.deleted_at is not None:
        raise NotFoundError(say("topicNotFound"))
    await _actor_in_place(resolver, place)
    instances = await AgentInstanceService(db).list_for_project(place.project_id)
    authors = {
        instance.handle: agent_instance_handle(instance.id) for instance in instances
    }
    native_author = (
        next((name for name, seat in authors.items() if seat == author), "")
        if author is not None
        else None
    )
    cursor = None
    if before is not None:
        try:
            cursor = service.decode_cursor(before)
        except ValueError:
            raise NotFoundError(say("cursorEventNotFound")) from None
    raw = await service.read(
        db,
        place.conversation_id,
        before=cursor,
        newest=True,
        author=native_author,
        limit=limit,
    )
    items = [as_block(item, place.conversation_id) for item in raw.items]
    for item in items:
        item["author"] = authors.get(item["author"], item["author"])
    old, more_old, boundary = await earlier_turns(
        db, place.conversation_id, before=cursor, author=author, limit=limit
    )
    records = await for_site(
        db,
        place.conversation_id,
        since=boundary,
        until=cursor[0] if cursor else None,
        author=author,
        limit=limit + 1,
        before=cursor,
    )
    for record in records:
        key = (
            datetime.fromisoformat(record["created_at"]),
            "platform",
            record["author"],
            uuid.UUID(record["id"]).int,
        )
        record["meta"]["cursor"] = service.encode_cursor(key)
    candidates = [*items, *old, *records]

    def order(item):
        return service.decode_cursor(item["meta"]["cursor"])

    candidates = sorted(
        (item for item in candidates if cursor is None or order(item) < cursor),
        key=order,
    )
    has_more = raw.next is not None or more_old or len(candidates) > limit
    items = candidates[-limit:]
    starts = await turn_starts(
        db,
        place.conversation_id,
        (uuid.UUID(i["turn_id"]) for i in items if i["turn_id"]),
    )
    return JSONResponse(
        ok(
            {
                **page(items, len(items)),
                "has_more": has_more,
                "oldest_id": items[0]["meta"]["cursor"] if items and has_more else None,
                "turn_starts": {
                    str(turn): at.isoformat() for turn, at in starts.items()
                },
            }
        )
    )
