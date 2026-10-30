"""The raw record of what a conversation's sessions did.

`GET /topics/{topic_id}/session-records`: every record the conversation's
sessions journaled (Claude Code's stream-json lines, Codex's app-server
notifications, pi's entries), scrubbed of credentials and otherwise as the
harness wrote them, oldest first and paged. `turn_id` narrows it to one turn.

`POST /topics/{topic_id}/session-records`: one status record the machine
itself reports for 现场 (a sync that failed). It is not a harness journal
entry; `harness` keeps it apart from every agent's run. It joins the turn the
checkpoint names (`X-Cheese-Turn`), and with no turn is drawn as a platform
line of its own.

The same door as 现场 (`topics_transcript.py`): whoever may read the
conversation's transcript may read its raw record.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.session_record_rows import hidden_private_memory, no_platform_reminders
from app.api.topics_deps import _actor_in_place
from app.api.write_access import Can, InChannel
from app.core.errors import NotFoundError
from app.core.sentences import say
from app.core.work_context import current_work_id
from app.domain.conversation.services import rooms_of_inner
from app.domain.project_permissions import Permission
from app.domain.room_task.place import place_or_404
from app.domain.session_record import service
from app.domain.session_record.views import lines

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
    lines: bool = False,
) -> JSONResponse:
    """The conversation's raw records after the cursor `after`, oldest first.

    `next` is the cursor of the last record on this page while there are more,
    and null once there are none. A cursor this route did not hand out is
    refused rather than read as "from the start", which a caller could not tell
    from a real page.

    `lines=true` answers in the shape a pulled transcript file holds instead of
    the raw one: one row per record with the harness-neutral `thread`, `role`,
    `kind` and `parts` fields of `session_record.views.line`, and the platform's
    own repeated words taken out of the text (`session_record_rows`). The raw
    record is not sent — a caller that wants it asks without `lines`."""
    place = await place_or_404(db, topic_id)
    if place.room.deleted_at is not None:
        raise NotFoundError(say("topicNotFound"))
    actor = await _actor_in_place(resolver, place)
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
        return JSONResponse(
            ok(_shaped(result["items"], result["next"], lines, actor.handle))
        )
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
    return JSONResponse(ok(_shaped(page.items, page.next, lines, actor.handle)))


def _shaped(items: list[dict], following: str | None, lines: bool, reader: str) -> dict:
    """A page as the caller asked for it: raw records, or transcript rows,
    either way without another person's private memory index."""
    items = hidden_private_memory(items, reader)
    if not lines:
        return {"items": items, "next": following}
    return {
        "items": _rows(items),
        "next": following,
    }


def _rows(items: list[dict]) -> list[dict]:
    """A page of records as transcript rows: normalized, reminders taken out.

    A caller paging reads each page on its own, and a subagent's thread can
    start on one page and go on in the next — which is why the CLI unions
    `subagent` over everything it pulled. A page holding the rest of a Codex
    child thread but not the record that declared it marks only what each
    record says about itself.
    """
    rows = lines(items)
    for row in rows:
        row["parts"], _ = no_platform_reminders(row["parts"])
    return rows


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


class MachineRecordIn(BaseModel):
    #: Only what the machine itself reports and 现场 has a line for. A harness's
    #: own journal is never written through this door.
    subtype: Literal["sync_failed"]
    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]


class MachineRecordOut(BaseModel):
    recorded: bool


@router.post(
    "/{topic_id}/session-records",
    **typed_response(MachineRecordOut),
)
async def note_machine_record(
    at: Annotated[InChannel, Can(Permission.record_machine_status, at="topic_id")],
    body: MachineRecordIn,
    db: DbSession,
) -> dict:
    """Let the machine put one line in 现场, in its own voice.

    The sync that pushes a turn's work runs after the turn has ended, so its
    failure is not in the agent's journal — and posting it into the room as a
    chat message told a person to do something only the machine could. Here it
    is what it is: one status record in the 现场 of the conversation the
    credential was minted for. It joins the turn the checkpoint names, so the
    failure sits in that turn's process view; with no turn (a sync outside any
    turn) 现场 draws it as a platform line of its own. A sibling task's branch
    may be what failed, and the text names it.
    """
    place, actor = at.place, at.actor
    if place.room.deleted_at is not None:
        raise NotFoundError(say("topicNotFound"))
    await service.note(
        db,
        place.conversation_id,
        agent_handle=actor.handle,
        turn_id=current_work_id.get(),
        record={
            "type": "system",
            "subtype": body.subtype,
            "message": body.text,
        },
    )
    await db.commit()
    return ok({"recorded": True})
