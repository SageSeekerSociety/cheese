"""Where the live document lands: the collaboration service's store.

Every new version of a document arrives here, whoever made it —
people typing in an editor, or a backend writer the service applied for it
(``living_doc.collab.replace``). One transaction records the Yjs state, the
Markdown exported from it, the version and its conversation event, and, for an
idempotent write, the operation's receipt. Nothing else writes the document
once it exists: anything that did would be overwritten by the next store.

The effects that tell the room (the event block, the running turn, the state
frame that refreshes the panels around the document) follow the commit.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_broker
from app.api.response import ok
from app.domain.agent.chat import ChatService
from app.domain.block.documents import (
    DocumentWriter,
    persisted_notice,
    tell_room_of_document,
)
from app.domain.block.models import Block
from app.domain.block.schemas import BlockOut
from app.domain.living_doc import collab
from app.domain.living_doc.models import Document
from app.domain.living_doc.schemas import document_snapshot
from app.domain.living_doc.services import DocumentJournal
from app.domain.room_task import naming
from app.domain.topic.doc_change import summarize_doc_change
from app.domain.topic.doc_checks import living_doc_warnings


@dataclass
class Stored:
    """What a store answers the service, and what it must announce."""

    answer: dict
    notice: Block | None = None
    changed: bool = False
    #: The notice is an earlier line extended, not a new one.
    merged: bool = False


def snapshot(doc: Document, operation_id: uuid.UUID | None) -> dict:
    out = document_snapshot(doc)
    out["operation_id"] = str(operation_id) if operation_id else None
    return ok(out, warnings=living_doc_warnings(doc.content))


async def store(
    db: AsyncSession,
    doc: Document,
    *,
    state: bytes,
    content: str | None,
    actors: list[str],
    operation: dict | None,
    quiet: bool = False,
    suggestions: list[dict] | None = None,
    requested_by: str | None = None,
    edits: list[dict] | None = None,
    suggested: bool = False,
    reason: str | None = None,
) -> Stored:
    """Record one store. ``content`` None stores the Yjs state alone. A
    ``quiet`` store records its version without a conversation event: the
    service's first conversion of a Markdown document, which only respells
    it.

    ``suggestions`` are the ones pending in the stored state, kept with it on
    every store. A ``suggested`` store proposed ``edits`` (each with its
    ``suggestion_id``) without changing the text: no version, only the line
    that says so. ``requested_by`` is who an edit was made for.
    """
    journal = DocumentJournal(db)
    await journal.lock(doc.id)
    claim = None
    operation_id = None
    if operation is not None:
        operation_id = uuid.UUID(operation["operation_id"])
        claim = await journal.claim(
            document_id=doc.id,
            actor=operation["actor"],
            action=operation["action"],
            operation_id=operation_id,
            payload=operation["payload"],
        )
    proposed = [e["suggestion_id"] for e in edits or [] if e.get("suggestion_id")]
    await journal.put_state(
        doc.id,
        state,
        suggestions,
        reasons={sid: reason for sid in proposed} if reason else None,
    )
    if claim is not None and claim.receipt is not None:
        # A replayed operation: its version is already recorded. The state the
        # service sent still holds it, so keeping that state loses nothing.
        return Stored(answer=claim.receipt)
    writer = DocumentWriter(db, summarize_doc_change)
    if suggested:
        notice = await writer.suggest(
            doc, actor=actors[0], suggestion_ids=proposed, reason=reason
        )
        return Stored(
            answer=snapshot(doc, None), notice=notice, merged=writer.notice_merged
        )
    if content is None:
        return Stored(answer={"doc_version": doc.version})
    recorded, notice = await writer.record(
        doc,
        content=content,
        actors=actors,
        operation_id=operation_id,
        quiet=quiet,
        requested_by=requested_by,
        edits=edits,
    )
    changed = recorded is not None
    if claim is None or operation is None or operation_id is None:
        return Stored(
            answer=snapshot(doc, None),
            notice=notice,
            changed=changed,
            merged=writer.notice_merged,
        )
    receipt = snapshot(doc, operation_id)
    await journal.finish(claim, receipt)
    return Stored(
        answer=receipt, notice=notice, changed=changed, merged=writer.notice_merged
    )


async def announce(doc: Document, stored: Stored, chat: ChatService) -> None:
    """After the commit: tell the document's open editors what the store
    changed, and, for a room's document, the room."""
    if stored.changed:
        # The editors already hold the text; what refreshes on this frame is
        # everything derived from the stored version: comment anchors, the
        # last edit, the history.
        await collab.tell(doc.id, {"type": "state", "resource": "doc"})
    if stored.changed:
        # A task's document rewritten: a moment its direction may show.
        naming.nudge_document(doc.id)
    room_id = doc.room_id
    if room_id is None:
        return
    broker = get_broker()
    if stored.notice is not None:
        # An extended line is replaced where it stands on every open page.
        await broker.publish(
            str(room_id),
            {
                "type": "block_updated" if stored.merged else "event_block",
                "block": BlockOut.model_validate(stored.notice).model_dump(mode="json"),
            },
        )
        if line := persisted_notice(stored.notice):
            await chat.notify_running_turn(room_id, line, blocks=[stored.notice.id])
    if stored.changed:
        # The room's overview shows what the document says.
        await broker.publish(str(room_id), {"type": "state", "resource": "doc"})


async def tell_origin_room(
    db: AsyncSession,
    conversation_id: uuid.UUID | None,
    doc: Document,
    actor: str,
    *,
    created: bool = False,
    edits: list[dict] | None = None,
    suggested: list[str] | None = None,
) -> None:
    """芝士 made or changed ``doc``, a document of the project's own, while
    working in ``conversation_id`` (a room, or one of its tasks): that
    conversation gets a line with the document on it. A room's own document
    is told by its store (`announce`); a change made from no conversation (a
    person's, or 芝士 answering on the document itself) is in the document's
    history and nowhere else."""
    if conversation_id is None or doc.room_id is not None:
        return
    from app.domain.room_task.place import PlaceResolver

    place = await PlaceResolver(db).conversation(conversation_id)
    if place is None:
        return
    line, merged = await tell_room_of_document(
        db,
        room_id=place.room_id,
        task_id=place.task_id,
        doc=doc,
        actor=actor,
        created=created,
        edits=edits,
        suggested=suggested,
    )
    await db.commit()
    await get_broker().publish(
        str(conversation_id),
        {
            "type": "block_updated" if merged else "event_block",
            "block": BlockOut.model_validate(line).model_dump(mode="json"),
        },
    )
