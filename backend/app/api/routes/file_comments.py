"""Comment threads on a room file.

A room file is what 芝士 delivers and a person reads: a report, a deck, a
budget. The same threads a document has hang on it, with the same replies,
resolution and operation-id discipline (``living_doc.comments``); what differs
is the anchor.

A document marks the words a thread is about in its own shared text, so the
mark travels with them. A file cannot: it is bytes, saved version by version.
So an opening comment carries ``anchor`` — the place in the file, in the
shapes a room message already quotes (``block.message_input``: a page, a cell,
a passage, an element, a region) together with the file version it was read at
— and ``quote``, how it reads. A later version may move that place; the thread
keeps saying which version it was written against, and the reader's panel is
what says whether the file has moved since.

Authorization is the room's, at the level commenting needs
(``Permission.comment``), the same as a document's threads: whoever may open
the room may say something about a file in it.

Not here yet: handing a thread to the room's AI teammate the way a document
thread is handed (``agent.document.thread``). That session reads and writes a
document; pointing it at a file is the next step.
"""

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import ActorResolverDep
from app.api.doc_access import require_role
from app.api.request_deps import DbSession
from app.api.response import ok, page, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import NotFoundError, UnprocessableEntityError, ValidationError
from app.core.sentences import say
from app.domain.agent.mentions import (
    CommentMentions,
    announce_comment_mentions,
    comment_notified,
)
from app.domain.agent_instance.adapters import project_team
from app.domain.block.message_input import QuotedContextIn
from app.domain.identity.actor import Actor
from app.domain.library import service as library
from app.domain.living_doc.comment_schemas import (
    CommentCreatedOut,
    ReplyIn,
    ThreadListOut,
    ThreadMutation,
    ThreadOut,
)
from app.domain.living_doc.comments import (
    CommentThreads,
    FileCommentOperations,
    OnRoomFile,
    comment_out,
)
from app.domain.mentions import canonicalize_refs
from app.domain.project.room_files import clean_artifact_path
from app.domain.project.services import refuse_writes_if_archived
from app.domain.project_permissions import Permission
from app.domain.room_task.place import Place, place_or_404, require_doc_writable

router = APIRouter(prefix="/topics", tags=["file-comments"])


class FileCommentIn(BaseModel):
    """A new thread on a file: what it says, where in the file it points, and
    how that place reads. The path is the file's address in the room."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    content: str = Field(default="", max_length=16000)
    quote: str | None = None
    anchor: QuotedContextIn | None = None


async def _in_room(
    db: DbSession, resolver: ActorResolverDep, topic_id: uuid.UUID
) -> tuple[Place, Actor]:
    place = await place_or_404(db, topic_id)
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    return place, actor


async def _reached(db, resolver, topic_id: uuid.UUID, raw_path: str, *, writes: bool):
    """The room, who is in it, and the file's cleaned path in it.

    ``writes`` also asks that their role lets them do what they are about to
    do — commenting is ``comment``, the same permission a document's threads
    ask for."""
    place, actor = await _in_room(db, resolver, topic_id)
    path = clean_artifact_path(raw_path)
    if library.library_name(path) is not None:
        # 资料库那一份的地址不是这个房间的文件；评论挂在房间自己的文件上。
        raise ValidationError(say("libraryOriginalReadOnly"))
    if library.is_message_attachment(path):
        raise UnprocessableEntityError(say("attachmentOriginalReadOnly"))
    if writes:
        await require_role(db, actor, place.project_id, Permission.comment)
        require_doc_writable(place)
        await refuse_writes_if_archived(db, place.project_id)
    return place, actor, path


def _file_out(place: Place, path: str) -> OnRoomFile:
    return OnRoomFile(room_id=place.room_id, path=path)


def _check_anchor(anchor, path: str) -> dict | None:
    """The anchor as stored, refused when it points at some other file.

    A thread on this file cannot be about those bytes: the anchor's own
    ``path`` is the file identity the preview read, and a mismatch means the
    caller is describing a different file than the one it names."""
    if anchor is None:
        return None
    stored = anchor.model_dump(mode="json")
    named = stored.get("path")
    if named is not None and named != path:
        raise ValidationError(say("commentAnchorOtherFile"))
    return stored


async def _as_stored(db, place: Place, content: str) -> str:
    """A comment's text as stored: a friendly @名字 becomes the person's
    ``<@handle>`` token, as every other text a person writes does."""
    if place.project_id is None:
        return content
    return await canonicalize_refs(db, place.project_id, content, team=project_team)


async def _with_notified(db, described: dict) -> dict:
    comments = [described["comment"]] + [r["comment"] for r in described["replies"]]
    told = await comment_notified(db, [uuid.UUID(c["id"]) for c in comments])
    for c in comments:
        c["notified"] = told.get(c["id"], [])
    return described


def _outside(told) -> dict:
    return {"mentioned_outside": told.outside.as_json()} if told.outside else {}


async def _announce(db, place: Place, path: str, comment) -> CommentMentions:
    return await announce_comment_mentions(
        db,
        place.project_id,
        place.room,
        comment,
        title=path,
        room_path=path,
        room_title=place.room.title,
    )


@router.get("/{topic_id}/files/comments/threads", **typed_response(ThreadListOut))
async def list_file_threads(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    path: Annotated[str, Query(min_length=1)],
) -> dict:
    """Every thread on this file with its replies, oldest first."""
    place, _, clean = await _reached(db, resolver, topic_id, path, writes=False)
    subject = _file_out(place, clean)
    service = CommentThreads(db)
    await service.lock(subject)
    items = [
        await _with_notified(db, await service.describe(c))
        for c in await service.roots(subject)
    ]
    await db.commit()
    return ok(page(items, len(items)))


@router.post(
    "/{topic_id}/files/comments",
    dependencies=[ROUTE_DECIDES],
    **typed_response(CommentCreatedOut),
)
async def add_file_comment(
    topic_id: uuid.UUID,
    body: FileCommentIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Start a comment thread on a room file.

    An archived room's files, and every file of an archived project, take no
    new thread — as its document does not."""
    place, actor, clean = await _reached(db, resolver, topic_id, body.path, writes=True)
    # 指着的东西得真的在：一份不在的文件上的评论谁也看不见。
    if not await asyncio.to_thread(
        library.room_file_exists, place.project_id, place.room_id, clean
    ):
        raise NotFoundError(say("fileNotFound"))
    content = body.content.strip()
    if not content:
        raise ValidationError(say("commentEmpty"))
    subject = _file_out(place, clean)
    comment = await CommentThreads(db).open(
        subject,
        author=actor.handle,
        content=await _as_stored(db, place, content),
        quote=(body.quote or "").strip() or None,
        anchor=_check_anchor(body.anchor, clean),
    )
    told = await _announce(db, place, clean, comment)
    payload = {**comment_out(comment), "notified": told.notified, **_outside(told)}
    await db.commit()
    return ok(payload)


@router.get(
    "/{topic_id}/files/comments/{comment_id}/thread",
    **typed_response(ThreadOut),
)
async def read_file_thread(
    topic_id: uuid.UUID,
    comment_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    path: Annotated[str, Query(min_length=1)],
) -> dict:
    place, _, clean = await _reached(db, resolver, topic_id, path, writes=False)
    subject = _file_out(place, clean)
    service = CommentThreads(db)
    await service.lock(subject)
    result = await _with_notified(
        db, await service.describe(await service.root(subject, comment_id))
    )
    await db.commit()
    return ok(result)


async def _mutate(
    db, resolver, topic_id, path: str, comment_id, body, action: str
) -> dict:
    """Apply one thread mutation once per operation id.

    The claim and the write share the thread's lock, as a document's do, so a
    retry after an unknown outcome replays the receipt instead of writing the
    reply twice."""
    place, actor, clean = await _reached(db, resolver, topic_id, path, writes=True)
    subject = _file_out(place, clean)
    operation = await FileCommentOperations(db).claim(
        subject,
        actor=actor.handle,
        action="file-comment-thread",
        operation_id=body.operation_id,
        payload={
            "comment_id": str(comment_id),
            "action": action,
            **body.model_dump(mode="json", exclude={"operation_id"}),
        },
    )
    if operation.receipt is not None:
        return operation.receipt
    reply = action == "reply"
    assert not reply or isinstance(body, ReplyIn)
    result = await CommentThreads(db).mutate(
        subject,
        comment_id=comment_id,
        author=actor.handle,
        expected_revision=body.expected_revision,
        action=action,
        content=await _as_stored(db, place, body.content.strip()) if reply else None,
    )
    told = None
    if reply:
        added = await CommentThreads(db).comment(
            uuid.UUID(result["replies"][-1]["comment"]["id"])
        )
        told = await _announce(db, place, clean, added)
    result = await _with_notified(db, result)
    receipt = ok(result)
    await FileCommentOperations(db).finish(operation, receipt)
    await db.commit()
    # The writer's own answer, not the operation's stored receipt: a replay
    # says nothing of who was outside.
    return (
        {**receipt, "data": {**receipt["data"], **_outside(told)}} if told else receipt
    )


@router.post(
    "/{topic_id}/files/comments/{comment_id}/replies",
    dependencies=[ROUTE_DECIDES],
    **typed_response(ThreadOut),
)
async def reply_to_file_thread(
    topic_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: ReplyIn,
    db: DbSession,
    resolver: ActorResolverDep,
    path: Annotated[str, Query(min_length=1)],
) -> dict:
    return await _mutate(db, resolver, topic_id, path, comment_id, body, "reply")


@router.post(
    "/{topic_id}/files/comments/{comment_id}/resolve",
    dependencies=[ROUTE_DECIDES],
    **typed_response(ThreadOut),
)
async def resolve_file_thread(
    topic_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: ThreadMutation,
    db: DbSession,
    resolver: ActorResolverDep,
    path: Annotated[str, Query(min_length=1)],
) -> dict:
    return await _mutate(db, resolver, topic_id, path, comment_id, body, "resolve")


@router.post(
    "/{topic_id}/files/comments/{comment_id}/reopen",
    dependencies=[ROUTE_DECIDES],
    **typed_response(ThreadOut),
)
async def reopen_file_thread(
    topic_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: ThreadMutation,
    db: DbSession,
    resolver: ActorResolverDep,
    path: Annotated[str, Query(min_length=1)],
) -> dict:
    return await _mutate(db, resolver, topic_id, path, comment_id, body, "reopen")
