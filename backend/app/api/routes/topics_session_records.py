"""The raw record of what a conversation's sessions did.

`GET /topics/{topic_id}/session-records`: every record the conversation's
sessions journaled (Claude Code's stream-json lines, Codex's app-server
notifications, pi's entries), scrubbed of credentials and otherwise as the
harness wrote them, oldest first and paged. `turn_id` narrows it to one turn.

The same door as 现场 (`topics_transcript.py`): whoever may read the
conversation's transcript may read its raw record.
"""

import uuid

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.topics_deps import _actor_in_place
from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.room_task.place import place_or_404
from app.domain.session_record import service

router = APIRouter(prefix="/topics", tags=["topics"])


@router.get("/{topic_id}/session-records", **typed_response(service.PageOut))
async def session_records(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    turn_id: uuid.UUID | None = None,
    after: str | None = Query(None, max_length=512),
    limit: int = Query(200, ge=1, le=500),
) -> JSONResponse:
    """The conversation's raw records after the cursor `after`, oldest first.

    `next` is the cursor of the last record on this page while there are more,
    and null once there are none. A cursor this route did not hand out is
    refused rather than read as "from the start", which a caller could not tell
    from a real page."""
    place = await place_or_404(db, topic_id)
    if place.room.deleted_at is not None:
        raise NotFoundError(say("topicNotFound"))
    await _actor_in_place(resolver, place)
    cursor = None
    if after is not None:
        try:
            cursor = service.decode_cursor(after)
        except ValueError:
            raise NotFoundError(say("cursorEventNotFound")) from None
    page = await service.read(
        db, place.conversation_id, turn_id=turn_id, after=cursor, limit=limit
    )
    # Rendered by the json module rather than the response model: a record
    # is whatever the harness wrote, and pydantic gives up on one nested
    # past about 250 levels, where json goes on.
    return JSONResponse(ok({"items": page.items, "next": page.next}))
