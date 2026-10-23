"""The 现场 timeline is a read projection of immutable harness records."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, page
from app.api.session_record_rows import no_platform_reminders, text_of
from app.api.topics_deps import _actor_in_place
from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.agent.harness.notices import without_reminders
from app.domain.agent.run_records import for_site
from app.domain.agent.turn_times import turn_starts
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle
from app.domain.room_task.place import place_or_404
from app.domain.session_record import service
from app.domain.session_record.history import earlier_turns
from app.domain.session_record.views import as_block

router = APIRouter(prefix="/topics", tags=["topics"])


def hide_platform_reminders(items: list[dict]) -> list[dict]:
    """现场 keeps every record, but does not repeat the platform's own reminders.

    The 现场 reads the harness journal, and every input in it carries the
    platform's words — the chat contract on every turn, a nudge when the room or
    the checklist went quiet. They are instructions to the model, not
    conversation, so showing them makes a reader scroll past the same paragraph
    over and over. `without_reminders` takes them out of the display only: the
    journal keeps every line (`meta.record` still has it) and the model already
    read them.

    A text part left with nothing is dropped, so a notice-only input leaves no
    empty row behind. The stripping itself lives in `session_record_rows`, which
    a pulled transcript goes through too — one set of words, one place.
    """
    for item in items:
        meta = item.get("meta")
        if not isinstance(meta, dict):
            continue
        parts = meta.get("parts")
        if not isinstance(parts, list) or not parts:
            if isinstance(item.get("content"), str):
                item["content"] = without_reminders(item["content"])
            continue
        kept, changed = no_platform_reminders(parts)
        if changed:
            meta["parts"] = kept
            item["content"] = text_of(kept)
    return items


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
    items = hide_platform_reminders(candidates[-limit:])
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
