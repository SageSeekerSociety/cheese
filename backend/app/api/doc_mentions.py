"""A person @-mentioned in a document's text is told so, once.

Every version of a document lands in one place (``doc_store.store``), whoever
wrote it: people typing in an editor, or a write the collaboration service
applied for a caller. That is where this runs, on the text before and after
the store:

- **Only what the store added.** A ``<@handle>`` that was already in the text
  before this store tells nobody, so the service storing the same document
  again, or someone fixing a typo next to a mention, never repeats it.
- **Once per document and person.** ``document_mentions`` records whom the
  document told. A mention removed and written again is new to the text but
  not to the person; they are not told twice. The same row is what readers go
  by to draw the name as a mention that reached them (``notified_handles``).
- **A person's save only.** A store holding an AI teammate's changes tells
  nobody: an agent's ``<@handle>`` in a document is a reference, not a call.
- **Only someone who can open it.** A task's document is read by whoever may
  enter the task's channel; a private channel's, only by the people seated in
  it (``TopicMemberService.reached``, as for a message). A person of the
  project mentioned in a document they cannot open is not told; the writer
  is, with an offer to add them (the same offer a message gets,
  ``agent.mentions.MentionedOutside``).
"""

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.text import markdown_preview
from app.domain.addressing import Event, Hand, address
from app.domain.agent.mentions import MentionedOutside
from app.domain.agent_instance.adapters import project_team
from app.domain.block.documents import whose_document
from app.domain.delivery.ledger import deliver
from app.domain.delivery_events import DeliveryEvent, event_id_for
from app.domain.identity.services import IdentityService
from app.domain.living_doc.models import Document, DocumentMention
from app.domain.membership.roster import roster_rows
from app.domain.notification.models import NotificationType
from app.domain.room_task.services import TaskService
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.services import TopicMemberService

_MENTION_RE = re.compile(r"<@([\w-]+)>")


def _handles(text: str) -> list[str]:
    return list(dict.fromkeys(_MENTION_RE.findall(text or "")))


async def notified_handles(session: AsyncSession, document_id: uuid.UUID) -> list[str]:
    """The people this document's text has told they were mentioned."""
    return list(
        (
            await session.scalars(
                select(DocumentMention.handle)
                .where(DocumentMention.document_id == document_id)
                .order_by(DocumentMention.notified_at, DocumentMention.handle)
            )
        ).all()
    )


async def _by_a_person(session: AsyncSession, actors: list[str]) -> str | None:
    """Who wrote this store, when only people did: the one with the most
    changes. None when an AI teammate or the platform had a hand in it."""
    if not actors or "system" in actors:
        return None
    for actor in actors:
        if await IdentityService(session).is_agent(actor):
            return None
    return actors[0]


def _line_of(text: str, handle: str, names: dict[str, str]) -> str:
    """The line the mention is on, as a notification shows it: plain text,
    each person by their name (an inbox row, a push and an email draw no
    tokens)."""
    token = f"<@{handle}>"
    line = next((line for line in text.splitlines() if token in line), text)
    named = _MENTION_RE.sub(lambda m: names.get(m.group(1), m.group(1)), line)
    return markdown_preview(named, 200)


async def tell_new_mentions(
    session: AsyncSession, doc: Document, *, before: str, after: str, actors: list[str]
) -> MentionedOutside | None:
    """Tell the people this store newly @-mentions, and return whom it
    mentions that cannot open the document (for its writer alone), if anyone.
    Runs in the store's transaction."""
    added = [h for h in _handles(after) if h not in set(_handles(before))]
    if not added:
        return None
    author = await _by_a_person(session, actors)
    if author is None:
        return None
    rows = await roster_rows(session, doc.project_id, team=project_team)
    people = {row["handle"] for row in rows if not row.get("agent")}
    named = [h for h in added if h in people and h != author]
    if not named:
        return None
    room_id, task_id = await whose_document(session, doc)
    room = await TopicRepository(session).get(room_id) if room_id else None
    members = TopicMemberService(session)
    # Whom a notice from this channel may reach (a private channel's own
    # people), the same rule a message's mention goes by.
    reach = await members.reached(room, named) if room is not None else named
    outside = [h for h in named if h not in reach]
    told = await _record(session, doc.id, reach, author)
    if told:
        await _tell(session, doc, told, author, after, rows, room, task_id)
    if not outside or room is None:
        return None
    return MentionedOutside(
        room_id=room.id,
        handles=outside,
        may_add=await members.may_bring_in(room, author),
        members_only=True,
    )


async def _record(
    session: AsyncSession, document_id: uuid.UUID, handles: list[str], author: str
) -> list[str]:
    """Write a row for each of ``handles`` this document has not told yet;
    the ones written are the ones to tell."""
    if not handles:
        return []
    written = await session.scalars(
        pg_insert(DocumentMention)
        .values(
            [
                {
                    "document_id": document_id,
                    "handle": h,
                    "notified_by": author,
                    "notified_at": datetime.now(UTC),
                }
                for h in handles
            ]
        )
        .on_conflict_do_nothing(index_elements=["document_id", "handle"])
        .returning(DocumentMention.handle)
    )
    fresh = set(written.all())
    return [h for h in handles if h in fresh]


async def _tell(
    session: AsyncSession,
    doc: Document,
    handles: list[str],
    author: str,
    text: str,
    rows: list[dict],
    room,
    task_id: uuid.UUID | None,
) -> None:
    who = next(
        (row["name"] for row in rows if row["handle"] == author and row.get("name")),
        author,
    )
    names = {row["handle"]: row.get("name") or row["handle"] for row in rows}
    title = doc.title
    where: dict[str, str] = {}
    if room is not None:
        where = {"topicId": str(room.id), "topicTitle": room.title}
        if task_id is not None:
            where["taskId"] = str(task_id)
            task = await TaskService(session).get(task_id)
            title = title or (task.title if task is not None else None)
    for handle in handles:
        # One event per document and person: the row above already makes it
        # once, and the ledger's key says the same if a store is retried.
        await deliver(
            session,
            DeliveryEvent(
                id=event_id_for(NotificationType.MENTION, f"doc:{doc.id}:{handle}"),
                type=NotificationType.MENTION,
                payload={
                    "projectId": str(doc.project_id),
                    "documentId": str(doc.id),
                    "documentTitle": title or "",
                    "author": author,
                    "authorName": who,
                    "content": _line_of(text, handle, names),
                    **where,
                },
                occurred_at=datetime.now(UTC),
            ),
            address(Event(mentioned=(handle,)), Hand.participant),
        )
