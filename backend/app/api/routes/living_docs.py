"""Living-document HTTP boundary.

The document itself is edited live in the collaboration service. This module
signs the ticket a browser opens it with, answers that service when it loads
and stores a document, and turns the writes that do not come from an editor
(an HTTP PUT — 芝士's ``cheese_doc(action=set)`` among them — and a restore) into
changes the service applies to the live document. None of them writes the
database directly: the service's store is the one place a version is recorded.
"""

import base64
import uuid
from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.deps import get_chat_service
from app.api.doc_access import (
    authorize_in_project,
    frozen,
    may_move_or_delete,
    reach,
    require_role,
    require_writable,
)
from app.api.doc_identity import operation_actor
from app.api.doc_mentions import notified_handles
from app.api.doc_store import announce, store, tell_origin_room
from app.api.request_deps import DbSession
from app.api.response import Page, ok, page, typed_response
from app.api.topics_deps import _actor_in_place
from app.api.write_access import ROUTE_DECIDES
from app.core import rank
from app.core.errors import (
    AuthenticationRequiredError,
    BaseError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
)
from app.core.sentences import say
from app.domain.agent.announce import who_started_the_open_turn
from app.domain.agent.chat import ChatService
from app.domain.agent_instance.adapters import project_team
from app.domain.assistant import changes
from app.domain.block import citations
from app.domain.block.documents import whose_document
from app.domain.canvas.block import canvas_ids
from app.domain.canvas.services import Settled, settle
from app.domain.conversation import services as conversations
from app.domain.identity.services import IdentityService
from app.domain.library import service as library
from app.domain.living_doc import collab, room_file_collab
from app.domain.living_doc import links as doc_links
from app.domain.living_doc.overview import overview_document
from app.domain.living_doc.schemas import (
    DocumentIn,
    DocumentRenameIn,
    DocumentRowOut,
    PassageEditsIn,
    RestoreIn,
    document_row,
    document_snapshot,
)
from app.domain.living_doc.services import DocumentJournal, Documents
from app.domain.mentions import (
    canonicalize_refs,
    conversation_refs,
    dangling_refs,
    dangling_refs_warning,
)
from app.domain.preview import office
from app.domain.project.address import Numbered, take_number
from app.domain.project.services import ProjectService, refuse_writes_if_archived
from app.domain.project_permissions import Permission
from app.domain.room_task.place import PlaceResolver, place_or_404
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
    place = await PlaceResolver(db).task_place(topic_id) or await place_or_404(
        db, topic_id
    )
    await _actor_in_place(resolver, place)
    if place.task is None:
        raise NotFoundError(say("channelHasNoDocument"))
    had_doc = place.task.document_id is not None
    document_id = await TaskService(db).ensure_document(place.task)
    if not had_doc:
        await db.commit()
    return ok({"id": str(document_id)})


@projects.get("/{project_id}/overview")
async def project_overview_document(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Which document holds the project's goals. It is made, empty, the first
    time anyone asks."""
    actor = await resolver.resolve(project_id=project_id, read_only=True)
    await authorize_in_project(resolver, actor, project_id)
    project = await ProjectService(db).get_or_404(project_id)
    if project.overview_document_id is not None:
        existing = await Documents(db).get(project.overview_document_id)
        if existing is not None:
            return ok({"id": str(existing.id)})
    doc = await overview_document(db, project_id)
    await db.commit()
    return ok({"id": str(doc.id)})


async def _with_ref_warnings(db, doc, answer: dict) -> dict:
    """``answer`` with a warning for each ``<#id>`` the document now holds that
    names nothing in its project. Read fresh from the text every time, so a
    reference that stopped resolving is said on the next read or write, and
    one that was fixed is not said again."""
    dangling = await dangling_refs(db, doc.project_id, doc.content or "")
    if not dangling:
        return answer
    warnings = [*(answer.get("warnings") or []), dangling_refs_warning(dangling)]
    return {**answer, "warnings": warnings}


async def _settled(
    db,
    resolver: ActorResolver,
    doc,
    text: str,
    *,
    actor: str,
    keep: Counter[uuid.UUID],
) -> Settled:
    """``text`` written into ``doc`` from outside an editor, its canvas blocks
    naming canvases of ``doc`` (`app.domain.canvas.services.settle`): a canvas
    of another document is copied for whoever may read that document."""

    async def readable(document_id: uuid.UUID) -> bool:
        try:
            await reach(db, resolver, document_id)
        except BaseError:
            return False
        return True

    return await settle(
        db,
        text,
        document_id=doc.id,
        project_id=doc.project_id,
        actor=actor,
        keep=keep,
        readable=readable,
    )


def _with_canvases(answer: dict, settled: list[Settled]) -> dict:
    """``answer`` naming the canvases made for ``new`` blocks, so the writer
    can fill them in."""
    made = [
        {"id": str(canvas_id), "title": title}
        for one in settled
        for canvas_id, title in one.made
    ]
    return {**answer, "canvases": made} if made else answer


def _origin(resolver: ActorResolver, reached_actor) -> uuid.UUID | None:
    """The conversation an agent writing a document of the project's own works
    in: a room, or one of its tasks."""
    return resolver.credential_conversation() if reached_actor.via == "cheese" else None


async def _authors(
    db, project_id: uuid.UUID, actor, origin: uuid.UUID | None
) -> tuple[str | None, str | None]:
    """The new document's authors, fixed from now on: the person it is theirs
    and the AI teammate writing it. A person making it is the one; 芝士 making
    it is the other, beside the person whose message opened the turn it
    writes in."""
    if actor.via != "cheese" and not await IdentityService(db).is_agent(actor.handle):
        return actor.handle, None
    asker = (
        await who_started_the_open_turn(db, project_id, origin, actor.handle)
        if origin is not None
        else None
    )
    return asker, actor.handle


@projects.get("/{project_id}/documents")
async def project_documents(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    """The project's own documents, the latest changed first, a page at a time
    (`next` is the cursor of the following page, null at the end). A room's
    living document is the room's and is not listed, nor is one a conversation
    keeps — except to 芝士 working in that conversation, whose own writing it
    is. The order is the one `app.core.rank` writes down, so the library page
    can merge these with the library's own pages into one list."""
    actor = await resolver.resolve(project_id=project_id, read_only=True)
    await authorize_in_project(resolver, actor, project_id)
    after = rank.decode(cursor) if cursor else None
    origin = _origin(resolver, actor)
    docs = await Documents(db).of_project(
        project_id,
        after=after,
        limit=limit + 1,
        also_of_room=await conversations.room_of(db, origin) if origin else None,
    )
    rows = [
        {**document_row(doc), "rank": rank.encode(1, doc.updated_at, str(doc.id))}
        for doc in docs[:limit]
    ]
    return ok({"data": rows, "next": rows[-1]["rank"] if len(docs) > limit else None})


@projects.post("/{project_id}/documents", dependencies=[ROUTE_DECIDES])
async def create_document(
    project_id: uuid.UUID,
    body: DocumentIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """A new document of the project's own: empty, with ``content``, or a copy
    of what another document says now (``copy_of``). 芝士 making one from a
    room puts it in that room as a line to open, and the document stays that
    conversation's until someone saves it to the library (#3828)."""
    actor = await resolver.resolve(project_id=project_id)
    await authorize_in_project(resolver, actor, project_id)
    await require_role(db, actor, project_id, Permission.edit_content)
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
    origin = _origin(resolver, actor)
    created_by, drafted_by = await _authors(db, project_id, actor, origin)
    project = await ProjectService(db).get(project_id)
    doc = await Documents(db).create(
        project_id=project_id,
        team_id=project.team_id if project is not None else None,
        title=title,
        author=actor.handle,
        created_by=created_by,
        drafted_by=drafted_by,
        number=await take_number(db, project_id, Numbered.document),
        room_id=await conversations.room_of(db, origin) if origin else None,
    )
    settled = await _settled(
        db, resolver, doc, content or "", actor=actor.handle, keep=Counter()
    )
    content = settled.text
    await db.commit()
    if content and content.strip():
        try:
            await collab.replace(
                doc.id,
                content=await canonicalize_refs(
                    db, project_id, content, team=project_team
                )
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
    await tell_origin_room(db, origin, doc, actor.handle, created=True)
    return await _with_ref_warnings(
        db, doc, _with_canvases(ok(document_row(doc)), [settled])
    )


@router.get("/{document_id}/about")
async def document_about(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """What the document is called and whose it is, even before anything is
    written in it."""
    return ok(document_row((await reach(db, resolver, document_id)).doc))


class LinkedDocumentOut(BaseModel):
    id: str
    number: int | None
    title: str


class CitingPlaceOut(BaseModel):
    kind: str
    id: str
    title: str
    room_id: str
    project_id: str


class LinkedFromOut(BaseModel):
    """A document, or a conversation's latest message, that references this
    one. ``kind`` says which; the fields of the other kind are absent."""

    kind: str
    id: str | None = None
    project_id: str | None = None
    number: int | None = None
    title: str | None = None
    updated_by: str | None = None
    updated_at: str | None = None
    conversation: CitingPlaceOut | None = None
    message_id: str | None = None
    excerpt: str | None = None
    author: str | None = None
    at: str | None = None
    count: int | None = None


class DocumentLinksOut(BaseModel):
    links_to: list[LinkedDocumentOut]
    linked_from: list[LinkedFromOut]


@router.get("/{document_id}/links", **typed_response(DocumentLinksOut))
async def document_links(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The documents this one links to, and the documents and conversations
    that link to it (#3827) — only those the caller may read. A conversation
    is one entry: the latest message there that references it, and how many
    do."""
    reached = await reach(db, resolver, document_id)

    async def readable(other: uuid.UUID) -> bool:
        try:
            await reach(db, resolver, other)
        except BaseError:
            return False
        return True

    async def title_of(doc) -> str:
        if doc.title:
            return doc.title
        task = await TaskService(db).of_document(doc.id)
        return task.title if task is not None else ""

    links_to = [
        {"id": str(d.id), "number": d.number, "title": await title_of(d)}
        for d in await doc_links.links_of(db, document_id)
        if await readable(d.id)
    ]
    linked_from: list[dict] = [
        {
            "kind": "document",
            "id": str(d.id),
            "project_id": str(d.project_id),
            "number": d.number,
            "title": await title_of(d),
            "updated_by": d.author,
            "updated_at": d.updated_at.isoformat(),
        }
        for d in await doc_links.linking_to(db, document_id)
        if await readable(d.id)
    ]
    # A team's own document is linked to from any of the team's projects.
    project_ids = (
        [reached.doc.project_id]
        if reached.doc.project_id is not None
        else await ProjectService(db).ids_of_team(reached.doc.team_id)
    )
    citing = await citations.citing_conversations(db, project_ids, document_id)
    places = await conversation_refs(db, [c.conversation_id for c in citing])
    rooms = await TopicService(db).get_many({p.room_id for p in places.values()})
    open_rooms: set[uuid.UUID] = set()
    for project_id in {p.project_id for p in places.values()}:
        open_rooms |= await resolver.readable_topic_ids(
            reached.actor,
            project_id=project_id,
            topics=[r for r in rooms if r.project_id == project_id],
        )
    for c in citing:
        place = places.get(c.conversation_id)
        if place is None or place.room_id not in open_rooms:
            continue
        linked_from.append(
            {
                "kind": "conversation",
                "conversation": {
                    "kind": place.kind.value,
                    "id": str(place.id),
                    "title": place.title,
                    "room_id": str(place.room_id),
                    "project_id": str(place.project_id),
                },
                "message_id": str(c.block_id),
                "excerpt": c.content[:400],
                "author": c.author,
                "at": c.at.isoformat(),
                "count": c.count,
            }
        )
    return ok({"links_to": links_to, "linked_from": linked_from})


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


@router.post(
    "/{document_id}/keep",
    dependencies=[ROUTE_DECIDES],
    **typed_response(DocumentRowOut),
)
async def keep_document(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Save a document 芝士 wrote in a conversation to the library, where
    everyone in the project finds it (#3828). Only a person does this: a
    session's credential reads the project and writes nothing at its level,
    because whether a document is worth keeping is a person's call."""
    reached = await _own(db, resolver, document_id)
    if reached.doc.project_id is None:
        raise NotFoundError(say("docNotFound"))
    await resolver.authorize_project(reached.actor, project_id=reached.doc.project_id)
    await Documents(db).keep(reached.doc)
    await db.commit()
    await db.refresh(reached.doc)
    return ok(document_row(reached.doc))


@rooms.get("/{topic_id}/documents", **typed_response(Page[DocumentRowOut]))
async def room_documents(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The documents 芝士 wrote in this channel or private chat, whether or
    not they are in the library yet, the newest first."""
    place = await place_or_404(db, topic_id)
    await _actor_in_place(resolver, place)
    docs = await Documents(db).of_room(place.room_id)
    return ok(page([document_row(doc) for doc in docs], len(docs)))


@router.patch("/{document_id}", dependencies=[ROUTE_DECIDES])
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


@router.delete("/{document_id}", dependencies=[ROUTE_DECIDES])
async def delete_document(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Delete a document of the project's own or the team's, its history and
    its comments; a team's by the person it is theirs or a team admin (#3774).
    Whoever has it open is told; what they type after that is not kept."""
    reached = await _own(db, resolver, document_id)
    if not await may_move_or_delete(db, reached):
        raise ForbiddenError(say("docMoveAuthorOrAdmin"))
    doc = reached.doc
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
    # Whose ``<@handle>`` in the text reached them: drawn as a mention, every
    # other one as a reference to the person.
    snapshot["notified_mentions"] = await notified_handles(db, doc.id)
    return await _with_ref_warnings(db, doc, ok(snapshot))


@router.get("/{document_id}/ticket")
async def document_ticket(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """A short-lived ticket that opens the live document.

    Whoever may read the document may open it; who may change it is decided
    here and carried in the ticket, because the service trusts nothing a
    browser says about itself. Read-only: a caller without a verified
    credential, an archived room, an archived project. Suggest-only: a person
    who may comment but not change the text (a mentor, #3830) writes
    suggestions of their own and nothing else.
    """
    reached = await reach(db, resolver, document_id)
    agent = await IdentityService(db).is_agent(reached.actor.handle)
    cannot_edit = not reached.actor.authenticated or await frozen(db, reached)
    suggest_only = (
        cannot_edit
        and reached.actor.authenticated
        and not agent
        and not await frozen(db, reached, Permission.comment)
    )
    read_only = cannot_edit and not suggest_only
    return ok(
        {
            "document": collab.document_name(reached.doc.id),
            "ticket": collab.sign_ticket(
                name=collab.document_name(reached.doc.id),
                handle=reached.actor.handle,
                agent=agent,
                read_only=read_only,
                suggest_only=suggest_only,
            ),
            "read_only": read_only,
            "suggest_only": suggest_only,
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


async def _agent_authors(db, document_id: uuid.UUID) -> list[str]:
    """The handles among the document's pending suggestions that are agent
    seats. A suggestion's author is the part of its id before the colon; the
    service cannot tell a seat's suggestion from a person's on its own, so the
    judgment is made here and handed to it. A pending suggestion the backend
    has not stored yet reads as a person's, which refuses rather than drops."""
    pending = await DocumentJournal(db).suggestions(document_id)
    handles = [str(item.get("author") or "") for item in pending]
    return sorted(await IdentityService(db).agents_among([h for h in handles if h]))


def _answering_agent(resolver: ActorResolver) -> str | None:
    """The agent a credential answering someone's question writes as; None for
    any other credential, which writes as its own actor."""
    delegation = resolver.delegation()
    if delegation is None:
        return None
    if delegation.agent is None:
        raise ForbiddenError("This credential names no agent to edit as")
    return delegation.agent


@router.put("/{document_id}", dependencies=[ROUTE_DECIDES])
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
    author = actor.handle
    # A 芝士 answering someone's question writes a whole document only as its
    # first version (drafting the project overview, `box.PRESETS["draft"]`): a
    # document somebody has written is changed passage by passage, so no one's
    # text is replaced wholesale by it. It writes as itself, as in `/edits`.
    if (answering := _answering_agent(resolver)) is not None:
        if (doc.content or "").strip():
            raise ForbiddenError(say("docWholeWriteNeedsBlank"))
        author = answering
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
    content = (
        await canonicalize_refs(db, doc.project_id, body.content, team=project_team)
        if doc.project_id is not None
        else body.content
    )
    base = await _base(db, doc, body.expected_version)
    settled = await _settled(
        db,
        resolver,
        doc,
        content,
        actor=author,
        keep=Counter(canvas_ids(doc.content or "")),
    )
    content = settled.text
    agent_authors = await _agent_authors(db, doc.id)
    # Nothing of this request may stay open across the call: the service's
    # store takes the document's lock in a transaction of its own.
    await db.commit()
    # Markdown is how this caller speaks; the document is blocks. A write that
    # would lose visible text on the way in is refused with what to change.
    stored = await collab.replace(
        doc.id,
        content=content,
        base=base,
        actor=author,
        operation=operation,
        check=True,
        agent_authors=agent_authors,
    )
    await db.refresh(doc)
    await tell_origin_room(db, _origin(resolver, actor), doc, author)
    return await _with_ref_warnings(db, doc, _with_canvases(stored, [settled]))


@router.post("/{document_id}/edits", dependencies=[ROUTE_DECIDES])
async def edit_doc_passages(
    document_id: uuid.UUID,
    body: PassageEditsIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Change passages of the document: each ``old`` (once in its Markdown)
    becomes ``new``, directly, or as suggestions when the caller asks for that
    (``mode: "suggest"``) — an AI teammate, or a person whose role lets them
    comment but not edit.

    For the room's agent and for people alike, each edits as themselves.
    Nothing is applied unless every edit can be; a refusal names the edit
    (``data.index``)."""
    reached = await reach(db, resolver, document_id, enforce=True)
    if not reached.actor.authenticated:
        raise AuthenticationRequiredError(say("docEditNeedsWriter"))
    # Suggesting a change is commenting on the text, not changing it: a mentor
    # suggests, and someone who may edit decides (#3830).
    await require_writable(
        db,
        reached,
        Permission.comment if body.mode == "suggest" else Permission.edit_content,
    )
    doc, actor = reached.doc, reached.actor
    if doc.version == 0:
        raise NotFoundError(say("docIsEmpty"))
    edits = [edit.model_dump() for edit in body.edits]
    settled: list[Settled] = []
    for edit in edits:
        # The same friendly @名字 / @话题名 → token rewrite a whole-document
        # write gets (``replace_document``): a passage is stored as the
        # document stores everything else.
        new = (
            await canonicalize_refs(db, doc.project_id, edit["new"], team=project_team)
            if doc.project_id is not None
            else edit["new"]
        )
        one = await _settled(
            db,
            resolver,
            doc,
            new,
            actor=actor.handle,
            keep=Counter(canvas_ids(edit["old"])),
        )
        edit["new"] = one.text
        settled.append(one)
    # A 芝士 answering someone's question on the document edits as itself.
    delegation = resolver.delegation()
    author = _answering_agent(resolver) or actor.handle
    editor_is_agent = await IdentityService(db).is_agent(author)
    # A suggestion is made only when it is asked for; an edit, by default, is
    # made.
    mode = "suggest" if body.mode == "suggest" else "direct"
    # The service's store takes the document's lock in a transaction of its own.
    agent_authors = await _agent_authors(db, doc.id)
    await db.commit()
    result = await collab.edit(
        doc.id,
        edits=edits,
        actor=author,
        mode=mode,
        reason=body.reason,
        editor_is_agent=editor_is_agent,
        agent_authors=agent_authors,
    )
    if delegation is not None:
        if delegation.user_id is not None and delegation.work:
            # Kept for whoever asked, to be shown and undone (``changes``).
            await changes.report(
                db,
                user_id=delegation.user_id,
                work=delegation.work,
                kind="doc",
                target=doc.id,
                payload={"document": str(doc.id), "mode": mode, "edits": edits},
            )
            await db.commit()
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
            if mode == "suggest"
            else None,
        )
    stored = result.get("stored") or {}
    if delegation is not None:
        await db.refresh(doc)
    return await _with_ref_warnings(
        db,
        doc,
        _with_canvases(
            ok(
                {
                    "mode": mode,
                    "edits": result.get("edits") or [],
                    "doc_version": (stored.get("data") or {}).get("doc_version"),
                },
                warnings=stored.get("warnings"),
            ),
            settled,
        ),
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


@router.post("/{document_id}/restore", dependencies=[ROUTE_DECIDES])
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
    agent_authors = await _agent_authors(db, doc.id)
    await db.commit()
    return await collab.replace(
        doc.id,
        content=content,
        base=base,
        actor=actor.handle,
        operation=operation,
        agent_authors=agent_authors,
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
    — for one never opened live — its Markdown, which the service converts and
    stores back, with the text it exports, before anyone edits it.

    Two kinds of thing arrive under this one route, told apart by the name:
    a living document (``doc:<id>``) and a room file being edited together
    (``roomfile:<id>``). The service treats the name as opaque, so they share
    a route; only here does the prefix mean anything.
    """
    if name.startswith(collab.ROOM_FILE_PREFIX + ":"):
        row = await room_file_collab.room_file_collab_named(db, name)
        if row is None:
            raise NotFoundError(say("roomFileNotFound"))
        return {
            "state": base64.b64encode(row.state).decode() if row.state else None,
            "content": row.content,
            # Room files version by content hash, not a counter: the service
            # only echoes this back on a refusal, and a stable handle to the
            # text is what a reader compares against anyway.
            "doc_version": 0,
        }
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
    #: An edit's passages, ``{old, new, suggestion_id?}``.
    edits: list[dict] | None = None
    #: The edit proposed its changes instead of making them.
    suggested: bool = False
    reason: str | None = None


@internal.put("/documents/{name}", dependencies=[ROUTE_DECIDES])
async def store_document(
    name: str,
    body: StoreIn,
    db: DbSession,
    _: ServiceOnly,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    if name.startswith(collab.ROOM_FILE_PREFIX + ":"):
        row = await room_file_collab.room_file_collab_named(db, name)
        if row is None:
            raise NotFoundError(say("roomFileNotFound"))
        actors = body.actors or ["system"]
        agent = await IdentityService(db).is_agent(actors[0])
        answer = await room_file_collab.store(
            db,
            row,
            state=base64.b64decode(body.state),
            content=body.content,
            actors=actors,
            suggestions=body.suggestions,
            author_kind="agent" if agent else "human",
            warm_preview=office.prewarm,
            file_store=library,
        )
        await db.commit()
        # A store that moved the text on: tell the open editors, the same frame
        # a living document's store sends. What refreshes on it is everything
        # derived from the new version — the history, the comment anchors, the
        # stored version other panes are reading.
        if answer["seq"] is not None:
            await collab.tell_named(name, {"type": "state", "resource": "doc"})
        return answer
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
        edits=body.edits,
        suggested=body.suggested,
        reason=body.reason,
    )
    await db.commit()
    await announce(doc, stored, chat)
    return stored.answer


def _doc_parts(payload: dict) -> list[str]:
    """A document write is undone whole; a suggestion is not undone here (it
    is turned down in the document)."""
    return ["text"] if payload.get("mode") == "direct" and payload.get("edits") else []


async def _write_back(db, resolver: ActorResolver, same, parts, redo: bool) -> dict:
    """Undo 芝士's writes of an answer into a document — each passage back to
    what it was, latest first — or redo them, as the person and with the
    checks a person's edit goes through. A passage changed since cannot be
    put back, and nothing is."""
    del parts  # a document write has one part
    reached = await reach(db, resolver, uuid.UUID(same[0].target), enforce=True)
    if not reached.actor.authenticated:
        raise AuthenticationRequiredError(say("docEditNeedsWriter"))
    await require_writable(db, reached, Permission.edit_content)
    doc, actor = reached.doc, reached.actor
    writes = [c for c in same if c.payload.get("mode") == "direct"]
    if redo:
        edits = [dict(e) for c in writes for e in c.payload.get("edits") or []]
    else:
        edits = [
            {"old": e["new"], "new": e["old"]}
            for c in reversed(writes)
            for e in reversed(c.payload.get("edits") or [])
        ]
    agent_authors = await _agent_authors(db, doc.id)
    await db.commit()
    try:
        await collab.edit(
            doc.id,
            edits=[{"old": e["old"], "new": e["new"]} for e in edits],
            actor=actor.handle,
            agent_authors=agent_authors,
        )
    except (ConflictError, UnprocessableEntityError) as exc:
        raise ConflictError(say("assistantChangeMoved")) from exc
    return {"document": str(doc.id)}


changes.register("doc", parts=_doc_parts, apply=_write_back)
