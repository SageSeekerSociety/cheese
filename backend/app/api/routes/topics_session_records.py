"""The raw record of what a conversation's sessions did.

`GET /topics/{topic_id}/session-records`: every record the conversation's
sessions journaled (Claude Code's stream-json lines, Codex's app-server
notifications, pi's entries), scrubbed of credentials and otherwise as the
harness wrote them, oldest first and paged. `turn_id` narrows it to one turn.

The same door as 现场 (`topics_transcript.py`): whoever may read the
conversation's transcript may read its raw record.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.topics_deps import _actor_in_place
from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.conversation.services import rooms_of_inner
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
    before: str | None = Query(None, max_length=512),
    newest: bool = False,
    author: str | None = Query(None, max_length=128),
    q: str | None = Query(None, min_length=1, max_length=1000),
    channel: bool = False,
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
    if channel:
        conversations = await rooms_of_inner(db, [place.room_id])
        result = await _channel_page(
            db,
            conversations,
            after=after,
            before=before,
            newest=newest,
            author=author,
            search=q,
            turn_id=turn_id,
            limit=limit,
        )
        return JSONResponse(ok(result))
    cursor = end = None
    if after is not None or before is not None:
        try:
            cursor = service.decode_cursor(after) if after is not None else None
            end = service.decode_cursor(before) if before is not None else None
        except ValueError:
            raise NotFoundError(say("cursorEventNotFound")) from None
    page = await service.read(
        db,
        place.conversation_id,
        turn_id=turn_id,
        after=cursor,
        before=end,
        newest=newest,
        author=author,
        search=q,
        limit=limit,
    )
    # Rendered by the json module rather than the response model: a record
    # is whatever the harness wrote, and pydantic gives up on one nested
    # past about 250 levels, where json goes on.
    return JSONResponse(ok({"items": page.items, "next": page.next}))


async def _channel_page(
    db, conversations, *, after, before, newest, author, search, turn_id, limit
):
    def position(token):
        if token is None:
            return None
        try:
            conversation, cursor = token.split(":", 1)
            return uuid.UUID(conversation), service.decode_cursor(cursor)
        except ValueError:
            raise NotFoundError(say("cursorEventNotFound")) from None

    low, high = position(after), position(before)

    def bound(cursor, conversation, lower):
        if cursor is None:
            return None
        source, key = cursor
        if source == conversation:
            return key
        inclusive = conversation > source if lower else conversation < source
        upper = not inclusive if lower else inclusive
        return (
            (key[0], "\U0010ffff", "\U0010ffff", 2**63 - 1)
            if upper
            else (key[0], "", "", -1)
        )

    found = []
    for conversation in conversations:
        page = await service.read(
            db,
            conversation,
            after=bound(low, conversation, True),
            before=bound(high, conversation, False),
            newest=newest,
            author=author,
            search=search,
            turn_id=turn_id,
            limit=limit + 1,
        )
        for item in page.items:
            item["conversation_id"] = str(conversation)
            key = (
                datetime.fromisoformat(item["at"]),
                item["harness"],
                item["agent_handle"],
                item["sequence"],
            )
            item["cursor"] = f"{conversation}:{service.encode_cursor(key)}"
            found.append(item)
    found.sort(
        key=lambda item: (
            datetime.fromisoformat(item["at"]),
            item["conversation_id"],
            item["harness"],
            item["agent_handle"],
            item["sequence"],
        ),
        reverse=newest,
    )
    items = found[:limit]
    following = items[-1]["cursor"] if len(found) > limit else None
    if newest:
        items.reverse()
    return {"items": items, "next": following}


@router.get("/{topic_id}/session-records/{cursor}", **typed_response(service.RecordOut))
async def session_record(
    topic_id: uuid.UUID, cursor: str, db: DbSession, resolver: ActorResolverDep
) -> JSONResponse:
    place = await place_or_404(db, topic_id)
    if place.room.deleted_at is not None:
        raise NotFoundError(say("topicNotFound"))
    await _actor_in_place(resolver, place)
    try:
        at, harness, agent, sequence = service.decode_cursor(cursor)
    except ValueError:
        raise NotFoundError(say("cursorEventNotFound")) from None
    result = await service.read(
        db, place.conversation_id, after=(at, harness, agent, sequence - 1), limit=1
    )
    if not result.items or (
        result.items[0]["at"],
        result.items[0]["harness"],
        result.items[0]["agent_handle"],
        result.items[0]["sequence"],
    ) != (at.isoformat(), harness, agent, sequence):
        raise NotFoundError(say("cursorEventNotFound"))
    return JSONResponse(ok(result.items[0]))
