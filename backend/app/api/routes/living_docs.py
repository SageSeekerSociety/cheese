"""Living-document HTTP boundary.

The document itself is edited live in the collaboration service. This module
signs the ticket a browser opens it with, answers that service when it loads
and stores a document, and turns the writes that do not come from an editor
(an HTTP PUT — 芝士's ``cheese_doc_set`` among them — and a restore) into
changes the service applies to the live document. None of them writes the
database directly: the service's store is the one place a version is recorded.
"""

import base64
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.deps import get_chat_service
from app.api.doc_access import authorize_in_project, frozen, reach, require_writable
from app.api.doc_edits import Decision, decide
from app.api.doc_identity import operation_actor
from app.api.doc_store import announce, store, tell_origin_room
from app.api.response import ok
from app.api.routes.topics import DbSession, _actor_in_place
from app.core.errors import (
    AuthenticationRequiredError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.core.redis import get_redis_client
from app.core.sentences import say
from app.domain.agent.chat import ChatService
from app.domain.block.documents import whose_document
from app.domain.identity.services import IdentityService
from app.domain.living_doc import collab, work_edits
from app.domain.living_doc.schemas import (
    DocumentIn,
    DocumentRenameIn,
    PassageEditsIn,
    RestoreIn,
    document_row,
    document_snapshot,
)
from app.domain.living_doc.services import DocumentJournal, Documents
from app.domain.mentions import canonicalize_refs
from app.domain.project.services import ProjectService, refuse_writes_if_archived
from app.domain.room_task.services import TaskService
from app.domain.topic.schemas import DocEditIn
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/documents", tags=["documents"])
rooms = APIRouter(prefix="/topics", tags=["topics"])
projects = APIRouter(prefix="/projects", tags=["documents"])


@rooms.get("/{topic_id}/document")
async def conversation_document(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Which document is this task's living document. It is made, empty, the
    first time anyone asks. Only a task has one."""
    topics = TopicService(db)
    place = await topics.place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    if place.task is None:
        raise NotFoundError(say("channelHasNoDocument"))
    document_id = await TaskService(db).ensure_document(place.task)
    await db.commit()
    return ok({"id": str(document_id)})


@projects.get("/{project_id}/overview")
async def project_overview_document(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Which document is the project's overview. It is made, empty, the first
    time anyone asks."""
    actor = await resolver.resolve(project_id=project_id, read_only=True)
    await authorize_in_project(resolver, actor, project_id)
    projects_ = ProjectService(db)
    doc = await projects_.overview_document(await projects_.get_or_404(project_id))
    await db.commit()
    return ok({"id": str(doc.id)})


def _origin(resolver: ActorResolver, reached_actor) -> uuid.UUID | None:
    """The conversation an agent writing a document of the project's own works
    in: a room, or one of its tasks."""
    return resolver.credential_conversation() if reached_actor.via == "cheese" else None


@projects.get("/{project_id}/documents")
async def project_documents(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The project's own documents, the latest changed first. A room's living
    document is the room's and is not listed."""
    actor = await resolver.resolve(project_id=project_id, read_only=True)
    await authorize_in_project(resolver, actor, project_id)
    docs = await Documents(db).of_project(project_id)
    return ok({"data": [document_row(doc) for doc in docs]})


@projects.post("/{project_id}/documents")
async def create_document(
    project_id: uuid.UUID,
    body: DocumentIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """A new document of the project's own: empty, with ``content``, or a copy
    of what another document says now (``copy_of``). 芝士 making one from a
    room puts it in that room as a line to open."""
    actor = await resolver.resolve(project_id=project_id)
    await authorize_in_project(resolver, actor, project_id)
    if not actor.authenticated:
        raise AuthenticationRequiredError(say("docEditNeedsWriter"))
    await refuse_writes_if_archived(db, project_id)
    title = body.title.strip()
    content = body.content
    if body.copy_of is not None:
        source = (await reach(db, resolver, body.copy_of)).doc
        if source.project_id != project_id:
            raise NotFoundError(say("docNotFound"))
        content = source.content
        if not title:
            # A task's document goes by its task's title.
            task = await TaskService(db).of_document(source.id)
            title = source.title or (task.title if task is not None else "")
    doc = await Documents(db).create(
        project_id=project_id, title=title, author=actor.handle
    )
    await db.commit()
    if content and content.strip():
        try:
            await collab.replace(
                doc.id,
                content=await canonicalize_refs(db, project_id, content)
                if body.copy_of is None
                else content,
                base=None,
                actor=actor.handle,
                # Stored text needs no check; Markdown from outside an editor does.
                check=body.copy_of is None,
            )
        except Exception:
            # Nothing made it in: the document goes, rather than stay empty
            # under a name its maker will not look for.
            await db.delete(doc)
            await db.commit()
            raise
        await db.refresh(doc)
    await tell_origin_room(
        db, _origin(resolver, actor), doc, actor.handle, created=True
    )
    return ok(document_row(doc))


@router.get("/{document_id}/about")
async def document_about(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """What the document is called and whose it is, even before anything is
    written in it."""
    return ok(document_row((await reach(db, resolver, document_id)).doc))


async def _own(db, resolver, document_id: uuid.UUID):
    """A document of the project's own that the caller may change. A task's
    living document is the task's: it goes by the task's name and goes with
    it."""
    reached = await reach(db, resolver, document_id, enforce=True)
    if not reached.actor.authenticated:
        raise AuthenticationRequiredError(say("docEditNeedsWriter"))
    if await whose_document(db, reached.doc) != (None, None):
        raise ForbiddenError(say("docBelongsToRoom"))
    if await ProjectService(db).is_overview(reached.doc.id):
        raise ForbiddenError(say("docIsProjectOverview"))
    await require_writable(db, reached)
    return reached


@router.patch("/{document_id}")
async def rename_document(
    document_id: uuid.UUID,
    body: DocumentRenameIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    doc = (await _own(db, resolver, document_id)).doc
    doc.title = body.title.strip()
    await db.commit()
    await db.refresh(doc)
    await collab.tell(doc.id, {"type": "state", "resource": "title"})
    return ok(document_row(doc))


@router.delete("/{document_id}")
async def delete_document(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Delete a document of the project's own, its history and its comments.
    Whoever has it open is told; what they type after that is not kept."""
    doc = (await _own(db, resolver, document_id)).doc
    await db.delete(doc)
    await db.commit()
    await collab.tell(document_id, {"type": "state", "resource": "deleted"})
    return ok({})


@router.get("/{document_id}")
async def get_document(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The document as last stored, with what is proposed and not yet
    decided; None while nothing has been written."""
    doc = (await reach(db, resolver, document_id)).doc
    if doc.version == 0:
        return ok(None)
    snapshot = document_snapshot(doc)
    snapshot["pending_suggestions"] = await DocumentJournal(db).suggestions(doc.id)
    return ok(snapshot)


@router.get("/{document_id}/ticket")
async def document_ticket(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """A short-lived ticket that opens the live document.

    Whoever may read the document may open it; who may change it is decided
    here and carried in the ticket, because the service trusts nothing a
    browser says about itself. Read-only: a caller without a verified
    credential, an archived room, an archived project.
    """
    reached = await reach(db, resolver, document_id)
    read_only = not reached.actor.authenticated or await frozen(db, reached)
    agent = await IdentityService(db).is_agent(reached.actor.handle)
    return ok(
        {
            "document": collab.document_name(reached.doc.id),
            "ticket": collab.sign_ticket(
                document_id=reached.doc.id,
                handle=reached.actor.handle,
                agent=agent,
                read_only=read_only,
            ),
            "read_only": read_only,
        }
    )


async def _base(db, doc, expected_version: int) -> str | None:
    """The document a writer based its change on: the version it read.

    Whether that is still the live document is for the service to decide, even
    when the stored version has moved past it. The stored version trails the
    live document by up to a store cycle; refused here, the writer would read
    it again and come back already behind. The service refuses only after
    storing what was typed since, so the writer reads the live document and
    its retry lands unless somebody types again.
    """
    current = doc.version
    if expected_version == current:
        return doc.content if current else None
    if expected_version == 0:
        return None
    read = None
    if expected_version < current:
        read = await DocumentJournal(db).version_content(doc.id, expected_version)
    if read is not None:
        return read
    # A version that was never recorded: nothing to compare.
    raise ConflictError(
        say("livingDocStale"),
        data={"doc_version": current},
    )


@router.put("/{document_id}")
async def replace_document(
    document_id: uuid.UUID,
    body: DocEditIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    reached = await reach(
        db, resolver, document_id, enforce=body.operation_id is not None
    )
    if body.operation_id is not None and not reached.actor.authenticated:
        # Not signed in says so before who may write this document does.
        raise AuthenticationRequiredError(say("docReceiptNeedsWriter"))
    await require_writable(db, reached)
    doc, actor = reached.doc, reached.actor
    operation = None
    if body.operation_id is not None:
        operation = {
            "actor": await operation_actor(db, actor),
            "action": "replace",
            "operation_id": str(body.operation_id),
            "payload": {
                "content": body.content,
                "expected_version": body.expected_version,
            },
        }
        if receipt := await _replayed(db, doc, operation):
            return receipt
    content = await canonicalize_refs(db, doc.project_id, body.content)
    base = await _base(db, doc, body.expected_version)
    # Nothing of this request may stay open across the call: the service's
    # store takes the document's lock in a transaction of its own.
    await db.commit()
    # Markdown is how this caller speaks; the document is blocks. A write that
    # would lose visible text on the way in is refused with what to change.
    stored = await collab.replace(
        doc.id,
        content=content,
        base=base,
        actor=actor.handle,
        operation=operation,
        check=True,
    )
    await db.refresh(doc)
    await tell_origin_room(db, _origin(resolver, actor), doc, actor.handle)
    return stored


@router.post("/{document_id}/edits")
async def edit_doc_passages(
    document_id: uuid.UUID,
    body: PassageEditsIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Change passages of the document: each ``old`` (once in its Markdown)
    becomes ``new``, directly or as suggestions (``app.api.doc_edits``).

    For the room's agent and for people alike: a person restoring one change
    edits directly, as themselves. Nothing is applied unless every edit can
    be; a refusal names the edit (``data.index``)."""
    reached = await reach(db, resolver, document_id, enforce=True)
    if not reached.actor.authenticated:
        raise AuthenticationRequiredError(say("docEditNeedsWriter"))
    await require_writable(db, reached)
    doc, actor = reached.doc, reached.actor
    if doc.version == 0:
        raise NotFoundError(say("docIsEmpty"))
    edits = [edit.model_dump() for edit in body.edits]
    # A 芝士 answering someone's question edits as itself, for that person,
    # directly: the person asked for exactly this change.
    delegation = resolver.delegation()
    if delegation is not None:
        if delegation.agent is None:
            raise ForbiddenError("This credential names no agent to edit as")
        author = delegation.agent
        decision = Decision(mode="direct", requested_by=actor.handle)
    else:
        author = actor.handle
        decision = await decide(
            db,
            doc=doc,
            actor=actor.handle,
            content=doc.content,
            edits=edits,
            asked=body.mode,
        )
    # The service's store takes the document's lock in a transaction of its own.
    await db.commit()
    result = await collab.edit(
        doc.id,
        edits=edits,
        actor=author,
        requested_by=decision.requested_by,
        mode=decision.mode,
        reason=body.reason,
    )
    if delegation is not None:
        await work_edits.record(get_redis_client(), delegation.work, edits)
    else:
        changed = result.get("edits") or edits
        await db.refresh(doc)
        await tell_origin_room(
            db,
            _origin(resolver, actor),
            doc,
            author,
            edits=changed,
            suggested=[e["suggestion_id"] for e in changed if e.get("suggestion_id")]
            if decision.mode == "suggest"
            else None,
        )
    stored = result.get("stored") or {}
    return ok(
        {
            "mode": decision.mode,
            "requested_by": decision.requested_by,
            "edits": result.get("edits") or [],
            "doc_version": (stored.get("data") or {}).get("doc_version"),
        },
        warnings=stored.get("warnings"),
    )


async def _replayed(db, doc, operation: dict) -> dict | None:
    return await DocumentJournal(db).replay(
        document_id=doc.id,
        actor=operation["actor"],
        action=operation["action"],
        operation_id=uuid.UUID(operation["operation_id"]),
        payload=operation["payload"],
    )


@router.get("/{document_id}/history")
async def document_history(
    document_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    after: int = Query(default=0, ge=0),
    newest: bool = Query(default=False),
    before: int | None = Query(default=None, ge=1),
    limit: int = Query(default=30, ge=1, le=50),
) -> dict:
    """The document's versions: oldest first from ``after``, or with ``newest``
    the latest ``limit`` first, those below ``before`` (the page's ``cursor``)
    when given."""
    doc = (await reach(db, resolver, document_id, enforce=True)).doc
    journal = DocumentJournal(db)
    if newest:
        rows = await journal.recent(doc.id, before=before, limit=limit)
        return ok({"versions": rows, "cursor": rows[-1]["version"] if rows else None})
    rows = await journal.history(doc.id, after=after)
    return ok({"versions": rows, "cursor": rows[-1]["version"] if rows else after})


@router.get("/{document_id}/compare")
async def compare_document_versions(
    document_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    before: int = Query(ge=0),
    after: int | None = Query(default=None, ge=1),
) -> dict:
    """Two versions of the document, for reading side by side: ``before``, and
    ``after`` (the current one when omitted). Version 0 is the empty document
    before anything was written — what a task started on an empty document
    is compared against."""
    doc = (await reach(db, resolver, document_id, enforce=True)).doc
    journal = DocumentJournal(db)
    newest = after or doc.version

    async def text_of(version: int) -> str:
        if version == 0:
            return ""
        content = await journal.version_content(doc.id, version)
        if content is None:
            raise NotFoundError(say("docVersionNotFound"))
        return content

    return ok(
        {
            "before": {"version": before, "content": await text_of(before)},
            "after": {"version": newest, "content": await text_of(newest)},
        }
    )


@router.get("/{document_id}/operations/{operation_id}")
async def document_receipt(
    document_id: uuid.UUID,
    operation_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    action: str = Query(default="replace", pattern="^(replace|restore)$"),
) -> dict:
    reached = await reach(db, resolver, document_id, enforce=True)
    if not reached.actor.authenticated:
        raise AuthenticationRequiredError(say("docReceiptNeedsWriter"))
    receipt = await DocumentJournal(db).receipt(
        document_id=reached.doc.id,
        actor=await operation_actor(db, reached.actor),
        action=action,
        operation_id=operation_id,
    )
    if receipt is None:
        raise NotFoundError(say("docReceiptNotFound"))
    return receipt


@router.post("/{document_id}/restore")
async def restore_document(
    document_id: uuid.UUID,
    body: RestoreIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    reached = await reach(db, resolver, document_id, enforce=True)
    if not reached.actor.authenticated:
        raise AuthenticationRequiredError(say("docRestoreNeedsWriter"))
    await require_writable(db, reached)
    doc, actor = reached.doc, reached.actor
    operation = {
        "actor": await operation_actor(db, actor),
        "action": "restore",
        "operation_id": str(body.operation_id),
        "payload": {"version": body.version, "expected_version": body.expected_version},
    }
    if receipt := await _replayed(db, doc, operation):
        return receipt
    content = await DocumentJournal(db).version_content(doc.id, body.version)
    if content is None:
        raise NotFoundError(say("docVersionNotFound"))
    base = await _base(db, doc, body.expected_version)
    await db.commit()
    return await collab.replace(
        doc.id,
        content=content,
        base=base,
        actor=actor.handle,
        operation=operation,
    )


# ---- The collaboration service's side --------------------------------------

internal = APIRouter(prefix="/internal/collab", tags=["collab"])


def _service(authorization: Annotated[str | None, Header()] = None) -> None:
    try:
        collab.verify_service(authorization)
    except collab.CollabRefused as exc:
        raise ForbiddenError(say("docCollabOnly")) from exc


ServiceOnly = Annotated[None, Depends(_service)]


async def _document(db, name: str):
    doc = await collab.document_named(db, name)
    if doc is None:
        raise NotFoundError(say("docNotFound"))
    return doc


@internal.get("/documents/{name}")
async def load_document(name: str, db: DbSession, _: ServiceOnly) -> dict:
    """What the service builds a live document from: the stored Yjs state, or
    — for a document never opened live — its Markdown, which the service
    converts and stores back, with the text it exports, before anyone edits
    it."""
    doc = await _document(db, name)
    state = await DocumentJournal(db).state(doc.id)
    return {
        "state": base64.b64encode(state).decode() if state is not None else None,
        "content": doc.content,
        "doc_version": doc.version,
    }


class StoreIn(BaseModel):
    state: str
    #: None: store the state alone (a conversion that changed no text).
    content: str | None = None
    #: Handles whose changes this store holds, the most changes first.
    actors: list[str] = Field(default_factory=list)
    operation: dict | None = None
    #: The service's first conversion of a Markdown document: it respells the
    #: text the way the document exports it, and is not news to the room.
    converted: bool = False
    #: Every suggestion pending in the stored state: ``{id, author, old, new}``.
    suggestions: list[dict] = Field(default_factory=list)
    #: An edit made for someone (``/edit``): who asked for it.
    requested_by: str | None = None
    #: An edit's passages, ``{old, new, suggestion_id?}``.
    edits: list[dict] | None = None
    #: The edit proposed its changes instead of making them.
    suggested: bool = False
    reason: str | None = None


@internal.put("/documents/{name}")
async def store_document(
    name: str,
    body: StoreIn,
    db: DbSession,
    _: ServiceOnly,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    doc = await _document(db, name)
    stored = await store(
        db,
        doc,
        state=base64.b64decode(body.state),
        content=body.content,
        # A change nobody on this instance made (another instance's typist,
        # under a shared Redis) is still a version; it is the platform's.
        actors=body.actors or ["system"],
        operation=body.operation,
        quiet=body.converted,
        suggestions=body.suggestions,
        requested_by=body.requested_by,
        edits=body.edits,
        suggested=body.suggested,
        reason=body.reason,
    )
    await db.commit()
    await announce(doc, stored, chat)
    return stored.answer
