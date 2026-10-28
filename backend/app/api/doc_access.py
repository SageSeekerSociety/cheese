"""Who may reach a document, and whether it takes writes.

A task's living document is read by whoever may enter the task's channel, and
written by the people working the task and the task's own session: what others
have to say about a task they say in the channel. A document 芝士 wrote in a
channel or a private chat is that conversation's until someone saves it to the
library: whoever may enter the conversation reads and writes it (#3828). A
document of the project's own — the project's goals and every saved one among
them — is the project's members', and 芝士's wherever in the project it works.
A document of no project is its team's (#3774): the team's members read and
write it — a person's own documents are their personal team's, so only theirs —
and 芝士 reads one only where a course it works in points at it (the teaching
settings' ``document_ids``). Every document route asks here, so they never
disagree.

`asked_of` is the one derivation the comment routes and the agent routes share:
from the caller's reach, the question an agent asks about this document. It
belongs with `Reached`, and the two modules that ask are route modules, which do
not import each other (C4 `routes-independent`).
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver
from app.auth.project_roles import require
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.agent.document.question import Asked
from app.domain.identity.actor import Actor
from app.domain.identity.services import IdentityService
from app.domain.living_doc.comments import OnDocument
from app.domain.living_doc.models import Document
from app.domain.living_doc.services import Documents
from app.domain.project.models import Project
from app.domain.project.services import ProjectArchivedError, refuse_writes_if_archived
from app.domain.project_permissions import Permission
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.place import Place, place_or_404, require_doc_writable
from app.domain.room_task.services import TaskService
from app.domain.task import teaching
from app.domain.team import permissions as team_seats
from app.domain.team.permissions import Permission as TeamPermission
from app.domain.team.services import team_service
from app.domain.user.services import user_by_handle


@dataclass(frozen=True)
class Reached:
    doc: Document
    actor: Actor
    #: The room the document is the living document of — or the room of the task
    #: it is the living document of, or the conversation that keeps it; None for
    #: a project's own.
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
    holds a task's document to its channel's roster even where channel access
    is not otherwise enforced (a write, or anything that acts as the caller)."""
    doc = await Documents(db).get(document_id)
    if doc is None:
        raise NotFoundError(say("docNotFound"))
    task = await TaskService(db).of_document(doc.id)
    if task is not None:
        place = await place_or_404(db, task.id)
        actor = await resolver.resolve(
            topic_id=place.conversation_id, project_id=place.project_id
        )
        await resolver.authorize_topic(
            actor, project_id=place.project_id, topic_id=place.room_id, enforce=enforce
        )
        return Reached(doc, actor, place, task, resolver.credential_conversation())
    if doc.room_id is not None and doc.kept_at is None:
        place = await place_or_404(db, doc.room_id)
        # Not resolved against the room's own conversation: 芝士 working in one
        # of the room's tasks or 支线 holds that conversation's credential, and
        # the room is what decides who reads the document.
        actor = await resolver.resolve(project_id=place.project_id)
        await resolver.authorize_topic(
            actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
        )
        return Reached(doc, actor, place, None, resolver.credential_conversation())
    if doc.project_id is None:
        return Reached(doc, await _team_reader(db, resolver, doc), None)
    actor = await resolver.resolve(project_id=doc.project_id)
    await authorize_in_project(resolver, actor, doc.project_id)
    return Reached(doc, actor, None)


async def _team_reader(
    db: AsyncSession, resolver: ActorResolver, doc: Document
) -> Actor:
    """Who reaches a team's own document: a member of the team, or 芝士 in a
    project whose course points at this document."""
    project_id = resolver.credential_project()
    if project_id is not None:
        actor = await resolver.resolve(project_id=project_id)
        project = await db.get(Project, project_id)
        taught = await teaching.for_project(session=db, project=project)
        if taught is None or str(doc.id) not in taught.teaching.document_ids:
            raise ForbiddenError(say("docTeamNotForAgent"))
        return actor
    actor = await resolver.resolve()
    resolver.reject_failed_credential(actor)
    if not actor.authenticated:
        raise AuthenticationRequiredError(say("docTeamMembersOnly"))
    if actor.via == "cheese" or not await is_team_member(db, doc.team_id, actor.handle):
        raise ForbiddenError(say("docTeamMembersOnly"))
    return actor


async def is_team_member(db: AsyncSession, team_id: int, handle: str) -> bool:
    user = await user_by_handle(db, handle)
    return user is not None and await team_service(db).is_team_member(team_id, user.id)


async def manages_team_documents(db: AsyncSession, team_id: int, handle: str) -> bool:
    user = await user_by_handle(db, handle)
    return user is not None and await team_seats.TEAM.allows(
        db, team_id, user.id, TeamPermission.manage_documents
    )


async def may_move_or_delete(db: AsyncSession, reached: Reached) -> bool:
    """A team's own document is deleted or moved by the person it is theirs
    (``created_by``: who made it, or who had 芝士 write it) or a team admin
    (#3774); a project's, by whoever may change it there."""
    doc = reached.doc
    if doc.project_id is not None:
        return True
    handle = reached.actor.handle
    return doc.created_by == handle or await manages_team_documents(
        db, doc.team_id, handle
    )


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
    # The least a write to a document needs; ``require_writable`` asks for what
    # the write itself needs.
    await resolver.authorize_project(
        actor, project_id=project_id, permission=Permission.comment
    )


async def require_role(
    db: AsyncSession, actor: Actor, project_id: uuid.UUID | None, permission: Permission
) -> None:
    """A person only does what their role lets them; an AI teammate writes as it
    was asked to, and its own gates are elsewhere. Nobody signed in reaches here
    only on the global sandbox token, which the project and room doors already
    let through as the trusted single-host credential: it has no seat to ask."""
    if not actor.authenticated or project_id is None:
        # A team's own document has no roles: its members all write it.
        return
    if actor.via != "cheese" and not await IdentityService(db).is_agent(actor.handle):
        await require(db, project_id, actor.handle, permission)


async def require_writable(
    db: AsyncSession,
    reached: Reached,
    permission: Permission = Permission.edit_content,
) -> None:
    """An archived room's document, and every document of an archived project,
    takes no more writes; and a person writes only what their role lets them
    (#3830): changing a document is ``edit_content``, commenting on it or
    suggesting a change is ``comment``. An AI teammate writes as it was asked
    to."""
    await require_role(db, reached.actor, reached.doc.project_id, permission)
    if reached.place is not None:
        require_doc_writable(reached.place)
    if reached.task is not None:
        if reached.task.status != TaskStatus.open:
            raise ValidationError(say("taskClosedNoTurn"))
        if (
            not TaskService.takes_part(reached.task, reached.actor.handle)
            and reached.scope != reached.task.id
        ):
            raise ForbiddenError(say("taskDocParticipantsOnly"))
    if reached.doc.project_id is None:
        if reached.actor.via == "cheese":
            # A course's agent reads what the course points it at; it writes none.
            raise ForbiddenError(say("docTeamNotForAgent"))
        return
    await refuse_writes_if_archived(db, reached.doc.project_id)


async def frozen(
    db: AsyncSession,
    reached: Reached,
    permission: Permission = Permission.edit_content,
) -> bool:
    try:
        await require_writable(db, reached, permission)
    except (ValidationError, ProjectArchivedError, ForbiddenError):
        return True
    return False


def asked_of(reached: Reached) -> Asked | None:
    """The question an agent-ask on this document is about: the document, the
    project it belongs to, and the task whose living document it is, if any.
    None for a team's own document: no AI teammate answers there (#3774)."""
    doc = reached.doc
    if doc.project_id is None:
        return None
    return Asked(
        project_id=doc.project_id,
        subject=OnDocument(doc.id),
        task_id=reached.task.id if reached.task is not None else None,
        # Where the thread is read: the task's own conversation, or the room a
        # document kept in one lives in. A document with neither is the
        # project's, read by its people.
        conversation_id=(
            reached.place.conversation_id if reached.place is not None else None
        ),
    )


async def ensure_team_documents(
    db: AsyncSession, document_ids: list[str], user_id: int
) -> None:
    """A course may point its AI teammates at a team's own document its writer
    can already open: one that exists, belongs to no project, and is of a team
    the writer is a member of. Anything else is refused by its id."""
    service = team_service(db)
    for raw in document_ids:
        try:
            doc = await Documents(db).get(uuid.UUID(raw))
        except ValueError:
            doc = None
        if (
            doc is None
            or doc.project_id is not None
            or not await service.is_team_member(doc.team_id, user_id)
        ):
            raise NotFoundError(say("docNotFound"), data={"id": raw})
