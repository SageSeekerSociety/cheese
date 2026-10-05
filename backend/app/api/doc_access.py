"""Who may reach a document, and whether it takes writes.

A room's living document is the room's: whoever may enter the room may read it
and write it. A document of the project's own, in no room, is the project's
members', and 芝士's wherever in the project it works. Every document route
asks here, so the two never disagree.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver
from app.core.errors import NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.identity.actor import Actor
from app.domain.living_doc.models import Document
from app.domain.living_doc.services import Documents
from app.domain.project.services import ProjectArchivedError, refuse_writes_if_archived
from app.domain.room_task.place import Place
from app.domain.topic.services import TopicService


@dataclass(frozen=True)
class Reached:
    doc: Document
    actor: Actor
    #: The room the document is the living document of; None for a project's own.
    place: Place | None


async def reach(
    db: AsyncSession,
    resolver: ActorResolver,
    document_id: uuid.UUID,
    *,
    enforce: bool = False,
) -> Reached:
    """The document and the caller, once the caller may reach it. ``enforce``
    holds a room's document to its roster even where room access is not
    otherwise enforced (a write, or anything that acts as the caller)."""
    doc = await Documents(db).get(document_id)
    if doc is None:
        raise NotFoundError(say("docNotFound"))
    if doc.room_id is None:
        actor = await resolver.resolve(project_id=doc.project_id)
        await authorize_in_project(resolver, actor, doc.project_id)
        return Reached(doc, actor, None)
    place = await TopicService(db).place_or_404(doc.room_id)
    actor = await resolver.resolve(topic_id=place.room_id, project_id=place.project_id)
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=enforce
    )
    return Reached(doc, actor, place)


async def authorize_in_project(
    resolver: ActorResolver, actor: Actor, project_id: uuid.UUID
) -> None:
    """Whether ``actor`` may reach the project's own documents: a member, or
    芝士 with the credential its session in one of the project's rooms runs
    with. That credential names the project it was issued in, and resolving
    it already refused any other; the agent is not a member, but the project's
    documents are where it keeps what it writes for the project. Taken off
    that room, the agent's credential reaches them no more."""
    if actor.via == "cheese" and resolver.origin_room() is not None:
        await resolver.refuse_unseated_agent(actor, project_id=project_id)
        return
    await resolver.authorize_project(actor, project_id=project_id)


async def require_writable(db: AsyncSession, reached: Reached) -> None:
    """An archived room's document, and every document of an archived project,
    takes no more writes."""
    if reached.place is not None:
        TopicService(db).require_doc_writable(reached.place)
    await refuse_writes_if_archived(db, reached.doc.project_id)


async def frozen(db: AsyncSession, reached: Reached) -> bool:
    try:
        await require_writable(db, reached)
    except (ValidationError, ProjectArchivedError):
        return True
    return False
