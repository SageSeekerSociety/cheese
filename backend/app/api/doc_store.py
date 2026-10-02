"""Where the live document lands: the collaboration service's store.

Every new version of a room's living document arrives here, whoever made it —
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
from app.domain.block.documents import DocumentWriter, persisted_notice
from app.domain.block.models import Block
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.doc_ai import acceptance
from app.domain.doc_ai.acceptance import ProposalAcceptance
from app.domain.living_doc.services import DocumentJournal, content_hash
from app.domain.topic import naming
from app.domain.topic.doc_change import summarize_doc_change
from app.domain.topic.doc_checks import living_doc_warnings
from app.domain.topic.services import TopicService


@dataclass
class Stored:
    """What a store answers the service, and what it must announce."""

    answer: dict
    notice: Block | None = None
    changed: bool = False


def snapshot(doc: Block, operation_id: uuid.UUID | None) -> dict:
    out = BlockOut.model_validate(doc).model_dump(mode="json")
    out["content_hash"] = content_hash(doc.content)
    out["operation_id"] = str(operation_id) if operation_id else None
    return ok(out, warnings=living_doc_warnings(doc.content))


async def store(
    db: AsyncSession,
    room_id: uuid.UUID,
    *,
    state: bytes,
    content: str | None,
    actors: list[str],
    operation: dict | None,
    quiet: bool = False,
) -> Stored:
    """Record one store. ``content`` None stores the Yjs state alone. A
    ``quiet`` store records its version without a conversation event: the
    service's first conversion of a Markdown document, which only respells
    it."""
    place = await TopicService(db).place_or_404(room_id)
    journal = DocumentJournal(db)
    await journal.lock(room_id)
    claim = None
    operation_id = None
    if operation is not None:
        operation_id = uuid.UUID(operation["operation_id"])
        claim = await journal.claim(
            room_id=room_id,
            actor=operation["actor"],
            action=operation["action"],
            operation_id=operation_id,
            payload=operation["payload"],
        )
    await journal.put_state(room_id, state)
    if claim is not None and claim.receipt is not None:
        # A replayed operation: its version is already recorded. The state the
        # service sent still holds it, so keeping that state loses nothing.
        return Stored(answer=claim.receipt)
    blocks = BlockRepository(db)
    if content is None:
        doc = await blocks.doc_root(room_id)
        return Stored(answer={"doc_version": doc.doc_version if doc else 0})
    doc, notice = await DocumentWriter(db, summarize_doc_change).record(
        room_id=room_id,
        project_id=place.project_id,
        content=content,
        actors=actors,
        operation_id=operation_id,
        quiet=quiet,
    )
    changed = doc is not None
    if doc is None:
        doc = await blocks.doc_root(room_id)
    if claim is None or operation is None or operation_id is None:
        return Stored(
            answer=snapshot(doc, None) if doc else {"doc_version": 0},
            notice=notice,
            changed=changed,
        )
    if doc is None:
        # An operation that wrote an empty document into a room that has none.
        receipt = {"doc_version": 0, "operation_id": str(operation_id)}
    elif claim.action == acceptance.ACTION:
        receipt = await ProposalAcceptance(db).complete(
            room_id=room_id,
            payload=operation["payload"],
            operation_id=operation_id,
            verified_actor=operation["actor"],
            doc=doc,
        )
    else:
        receipt = snapshot(doc, operation_id)
    await journal.finish(claim, receipt)
    return Stored(answer=receipt, notice=notice, changed=changed)


async def announce(room_id: uuid.UUID, stored: Stored, chat: ChatService) -> None:
    """After the commit: tell the room what the store changed."""
    broker = get_broker()
    if stored.notice is not None:
        await broker.publish(
            str(room_id),
            {
                "type": "event_block",
                "block": BlockOut.model_validate(stored.notice).model_dump(mode="json"),
            },
        )
        if line := persisted_notice(stored.notice):
            await chat.notify_running_turn(room_id, line, blocks=[stored.notice.id])
    if stored.changed:
        # The editors already hold the text; what refreshes on this frame is
        # everything derived from the stored version — comment anchors, the
        # document AI's source, the overview.
        await broker.publish(str(room_id), {"type": "state", "resource": "doc"})
        naming.nudge(room_id, "signal")
