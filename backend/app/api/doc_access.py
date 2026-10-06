"""Who may reach a document, and whether it takes writes.

A task's living document is read by whoever may enter the task's channel, and
written by the people working the task and the task's own session: what others
have to say about a task they say in the channel. A document of the project's
own — the project overview among them — is the project's members', and 芝士's
wherever in the project it works. Every document route asks here, so they never
disagree.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.identity.actor import Actor
from app.domain.living_doc.models import Document
from app.domain.living_doc.services import Documents
from app.domain.project.services import ProjectArchivedError, refuse_writes_if_archived
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.place import Place
from app.domain.room_task.services import TaskService
from app.domain.topic.services import TopicService


@dataclass(frozen=True)
class Reached:
    doc: Document
    actor: Actor
    #: The room the document is the living document of — or the room of the task
    #: it is the living document of; None for a project's own.
    place: Place | None
    #: The task whose living document this is.
    task: Task | None = None
    #: The conversation the caller's credential was minted in, when it is a
    #: session's: a task's own session writes its task's document.
    scope: uuid.UUID | None = None


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
    task = await TaskService(db).of_document(doc.id) if doc.room_id is None else None
    if task is not None:
        place = await TopicService(db).place_or_404(task.id)
        actor = await resolver.resolve(
            topic_id=place.conversation_id, project_id=place.project_id
        )
        await resolver.authorize_topic(
            actor, project_id=place.project_id, topic_id=place.room_id, enforce=enforce
        )
        return Reached(doc, actor, place, task, resolver.credential_conversation())
    if doc.room_id is None:
        actor = await resolver.resolve(project_id=doc.project_id)
        await authorize_in_project(resolver, actor, doc.project_id)
        return Reached(doc, actor, None)
    place = await TopicService(db).place_or_404(doc.room_id)
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
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
    if actor.via == "cheese" and resolver.credential_conversation() is not None:
        await resolver.refuse_unseated_agent(actor, project_id=project_id)
        return
    await resolver.authorize_project(actor, project_id=project_id)


async def require_writable(db: AsyncSession, reached: Reached) -> None:
    """An archived room's document, and every document of an archived project,
    takes no more writes."""
    if reached.place is not None:
        TopicService(db).require_doc_writable(reached.place)
    if reached.task is not None:
        if reached.task.status != TaskStatus.open:
            raise ValidationError(say("taskClosedNoTurn"))
        if (
            not TaskService.takes_part(reached.task, reached.actor.handle)
            and reached.scope != reached.task.id
        ):
            raise ForbiddenError(say("taskDocParticipantsOnly"))
    await refuse_writes_if_archived(db, reached.doc.project_id)


async def frozen(db: AsyncSession, reached: Reached) -> bool:
    try:
        await require_writable(db, reached)
    except (ValidationError, ProjectArchivedError, ForbiddenError):
        return True
    return False
