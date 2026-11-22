"""已读: who has read which messages of a conversation.

A page reports the messages it showed a person while it was in the
foreground; anybody who can read the conversation asks who has read what in
it. An AI teammate's reads are not reported here: the platform records them
when a turn's input reaches its session (`block.message_reads`).

The module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import AS_ONESELF
from app.core.errors import AuthenticationRequiredError
from app.core.sentences import say
from app.domain.block import message_reads
from app.domain.room_task.place import place_or_404

router = APIRouter(prefix="/topics", tags=["topics"])

#: Block numbers ``[first, last]``, both included.
Stretch = Annotated[
    list[Annotated[int, Field(ge=1)]], Field(min_length=2, max_length=2)
]


class ReadsIn(BaseModel):
    """The block numbers a page showed its person, as stretches."""

    ranges: list[Stretch] = Field(
        min_length=1, max_length=message_reads.MAX_REPORTED_RANGES
    )


class ReaderReads(BaseModel):
    reader: str
    #: ``[first, last]`` stretches of block numbers this reader has read.
    ranges: list[list[int]]


class ReadsOut(BaseModel):
    readers: list[ReaderReads]


class ReadsRecorded(BaseModel):
    #: The caller's stretches that grew with this report; empty when every
    #: number in it was already read.
    ranges: list[list[int]]


@router.post(
    "/{topic_id}/reads",
    dependencies=[AS_ONESELF],
    **typed_response(ReadsRecorded),
)
async def report_reads(
    topic_id: uuid.UUID, body: ReadsIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The signed-in person has seen these messages of a conversation (a
    room's main line, a task's, a 支线's): their page showed them on screen
    while it was in the foreground.

    Only a person reports for themselves, with their own sign-in: an AI
    teammate's credential, or a person's own 芝士 acting for them, has not
    seen anyone's screen. Reporting again what was read changes nothing.
    Whoever has the conversation open is told (a ``reads`` frame)."""
    place = await place_or_404(db, topic_id, member=resolver.signed_in_handle())
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    if actor.via != "token":
        raise AuthenticationRequiredError(say("signInForRoom"))
    await resolver.authorize_topic(
        actor, project_id=place.project_id, room=place.room, enforce=True
    )
    grew = await message_reads.record(
        db,
        place.conversation_id,
        actor.handle,
        [(first, last) for first, last in body.ranges],
    )
    await db.commit()
    return ok({"ranges": [list(s) for s in grew]})


@router.get("/{topic_id}/reads", **typed_response(ReadsOut))
async def list_reads(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    first: Annotated[int, Query(ge=1)],
    last: Annotated[int, Query(ge=1)],
) -> dict:
    """Who has read which of this conversation's blocks numbered
    ``first..last``: per reader — people and AI teammates alike — their read
    stretches within that span. A page holding those blocks tells each
    message's readers by its number (`Block.seq`).

    Per reader rather than per message: a channel's sixty members each send a
    stretch or two however many messages the page holds. Anyone who can read
    the conversation may ask; nobody else learns anything about it."""
    place = await place_or_404(db, topic_id, member=resolver.signed_in_handle())
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, room=place.room, enforce=True
    )
    if last < first:
        return ok({"readers": []})
    readers = await message_reads.reads_between(db, place.conversation_id, first, last)
    return ok({"readers": readers})
