"""Addresses people can say: `/projects/<slug>/tasks/318` and the like.

The browser turns such an address into the ids every other route takes, and
turns an address made of ids (an old link, or a link the app builds) into the
short one. Both answer only to someone who may see the thing addressed, and to
anyone else as if it did not exist. The rules are in
`app/domain/project/address.py`.

  GET /addresses/projects/{ref}                     a slug, old slug or id → the project
  GET /addresses/projects/{ref}/{kind}/{number}     a number → the thing
  GET /addresses/of/{kind}/{id}                     a thing → its short address
  PUT /projects/{project_id}/slug                   rename the project in addresses
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.doc_access import reach
from app.api.response import ok
from app.api.routes.topics import _actor_in_place
from app.core.db import get_db
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.membership.services import MemberService
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
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/addresses", tags=["addresses"])
projects = APIRouter(prefix="/projects", tags=["addresses"])

DbSession = Annotated[AsyncSession, Depends(get_db)]

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
            place = await TopicService(db).place_or_404(thing.id)
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


class SlugIn(BaseModel):
    slug: str


@projects.put("/{project_id}/slug")
async def set_project_slug(
    project_id: uuid.UUID, body: SlugIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Rename the project in addresses. Its managers' alone; the old name keeps
    leading here and no other project can take it."""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await MemberService(db).require_manager(project_id, actor)
    project = await ProjectService(db).get_or_404(project_id)
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
