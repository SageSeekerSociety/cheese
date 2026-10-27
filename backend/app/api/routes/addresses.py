"""Addresses people can say: `/projects/<slug>/tasks/318` and the like.

The browser turns such an address into the ids every other route takes, and
turns an address made of ids (an old link, or a link the app builds) into the
short one. Both answer only to someone who may see the thing addressed, and to
anyone else as if it did not exist. The rules are in
`app/domain/project/address.py`.

  GET /addresses/projects/{ref}                     a slug, old slug or id → the project
  GET /addresses/projects/{ref}/{kind}/{number}     a number → the thing
  GET /addresses/of/{kind}/{id}                     a thing → its short address
  GET /addresses/refs?ids=…                         `<#id>` references → what each names
  PUT /projects/{project_id}/slug                   rename the project in addresses
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, RootModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.doc_access import reach
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.topics_deps import _actor_in_place
from app.api.write_access import Can, InProject
from app.core.errors import (
    BaseError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.living_doc.services import Documents
from app.domain.membership.services import MemberService
from app.domain.mentions import conversation_refs
from app.domain.project.address import (
    Addressed,
    Numbered,
    SlugError,
    project_by_ref,
    rename_slug,
    thing_by_id,
    thing_by_number,
)
from app.domain.project.services import ProjectService
from app.domain.project_permissions import Permission
from app.domain.room_task.place import place_or_404
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/addresses", tags=["addresses"])
projects = APIRouter(prefix="/projects", tags=["addresses"])


_NOT_FOUND = "Not found"


async def _readable_project(db: AsyncSession, resolver: ActorResolver, ref: str):
    """The project ``ref`` names, if the caller may see it."""
    project = await project_by_ref(db, ref)
    if project is None:
        raise NotFoundError("Project not found")
    actor = await resolver.resolve(project_id=project.id, read_only=True)
    try:
        await resolver.authorize_project(actor, project_id=project.id)
    except ForbiddenError as refused:
        raise NotFoundError("Project not found") from refused
    return project


def _address(slug: str, thing: Addressed) -> dict:
    """What the browser needs to show one numbered thing: its id, the room it
    hangs in (a task's channel; a channel is its own), and its short form."""
    return {
        "project_id": str(thing.project_id),
        "slug": slug,
        "kind": thing.kind.value,
        "id": str(thing.id),
        "room_id": str(thing.room_id) if thing.room_id is not None else None,
        "number": thing.number,
    }


async def _authorize_thing(
    db: AsyncSession, resolver: ActorResolver, thing: Addressed
) -> None:
    """Whether the caller may see this one thing — a channel only its members
    see hides its number as it hides itself."""
    try:
        if thing.kind is Numbered.document:
            await reach(db, resolver, thing.id)
        else:
            place = await place_or_404(db, thing.id)
            await _actor_in_place(resolver, place)
    except ForbiddenError as refused:
        raise NotFoundError(_NOT_FOUND) from refused


@router.get("/projects/{ref}")
async def resolve_project(ref: str, db: DbSession, resolver: ActorResolverDep) -> dict:
    """The project a slug, a slug it once had, or its id names; ``slug`` is
    the name it goes by now."""
    project = await _readable_project(db, resolver, ref)
    return ok({"id": str(project.id), "slug": project.slug})


@router.get("/projects/{ref}/{kind}/{number}")
async def resolve_number(
    ref: str,
    kind: Numbered,
    number: int,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """The task, document or channel with this number in this project."""
    project = await _readable_project(db, resolver, ref)
    thing = await thing_by_number(db, project.id, kind, number)
    if thing is None:
        raise NotFoundError(_NOT_FOUND)
    await _authorize_thing(db, resolver, thing)
    return ok(_address(project.slug, thing))


@router.get("/of/{kind}/{thing_id}")
async def address_of(
    kind: Numbered, thing_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The short address of the task, document or channel with this id, in
    the project it is in now. ``number`` is null for one that has none yet
    (or never will: a private chat, a task's own document)."""
    thing = await thing_by_id(db, kind, thing_id)
    if thing is None:
        raise NotFoundError(_NOT_FOUND)
    await _authorize_thing(db, resolver, thing)
    project = await ProjectService(db).get_or_404(thing.project_id)
    return ok(_address(project.slug, thing))


#: How many references one read answers: a page of chat or a document names
#: far fewer, and a longer list is somebody asking for something else.
REFS_PER_READ = 100


class ConversationRefOut(BaseModel):
    kind: str
    title: str
    room_id: str
    project_id: str


class ConversationRefs(RootModel[dict[str, ConversationRefOut]]):
    """What each id that names something names, by id."""


@router.get("/refs", **typed_response(ConversationRefs))
async def resolve_refs(
    db: DbSession,
    resolver: ActorResolverDep,
    ids: Annotated[str, Query(max_length=REFS_PER_READ * 37)] = "",
) -> dict:
    """What each ``<#id>`` in ``ids`` (comma-separated) names: a channel, a
    task, a 支线 or a document, its title and the room it is in. A channel, task
    or 支线 the caller may not see is absent from the answer, like an id that
    names nothing — so a private channel is not told apart from one that was
    never made. A document the caller may not read is answered as
    ``unreadable``, without its title: a document is linked to by name, and
    the reader is told the link is there and closed to them (#3827)."""
    wanted: list[uuid.UUID] = []
    for raw in ids.split(",")[:REFS_PER_READ]:
        try:
            wanted.append(uuid.UUID(raw.strip()))
        except ValueError:
            continue
    refs = await conversation_refs(db, wanted)
    by_project: dict[uuid.UUID, set[uuid.UUID]] = {}
    for ref in refs.values():
        by_project.setdefault(ref.project_id, set()).add(ref.room_id)
    readable: set[uuid.UUID] = set()
    for project_id, room_ids in by_project.items():
        actor = await resolver.resolve(project_id=project_id, read_only=True)
        rooms = await TopicService(db).get_many(room_ids)
        readable |= await resolver.readable_topic_ids(
            actor, project_id=project_id, topics=rooms
        )
    answer = {
        str(ref.id): {
            "kind": ref.kind.value,
            "title": ref.title,
            "room_id": str(ref.room_id),
            "project_id": str(ref.project_id),
        }
        for ref in refs.values()
        if ref.room_id in readable
    }
    for doc_id in [i for i in wanted if i not in refs]:
        answer.update(await _document_ref(db, resolver, doc_id))
    return ok(answer)


async def _document_ref(
    db: AsyncSession, resolver: ActorResolver, document_id: uuid.UUID
) -> dict[str, dict]:
    """A document's entry in the answer: its title when the caller may read
    it, ``unreadable`` when not; nothing when there is no such document."""
    doc = await Documents(db).get(document_id)
    if doc is None:
        return {}
    try:
        reached = await reach(db, resolver, document_id)
    except BaseError:
        return {
            str(document_id): {
                "kind": "unreadable",
                "title": "",
                "room_id": "",
                "project_id": str(doc.project_id),
            }
        }
    title = doc.title or (reached.task.title if reached.task is not None else "")
    return {
        str(document_id): {
            "kind": "document",
            "title": title,
            "room_id": str(doc.room_id) if doc.room_id else "",
            "project_id": str(doc.project_id),
        }
    }


class SlugIn(BaseModel):
    slug: str


@projects.put("/{project_id}/slug")
async def set_project_slug(
    at: Annotated[InProject, Can(Permission.edit_settings)],
    body: SlugIn,
    db: DbSession,
) -> dict:
    """Rename the project in addresses. Its managers' alone; the old name keeps
    leading here and no other project can take it."""
    project_id = at.project.id
    await MemberService(db).require(project_id, at.actor, Permission.edit_settings)
    project = at.project
    try:
        slug = await rename_slug(db, project, body.slug)
    except SlugError as error:
        if error.reason == "taken":
            raise ConflictError(say("projectSlugTaken")) from error
        if error.reason == "reserved":
            raise ValidationError(say("projectSlugReserved")) from error
        raise ValidationError(say("projectSlugFormat")) from error
    await db.commit()
    return ok({"id": str(project.id), "slug": slug})
