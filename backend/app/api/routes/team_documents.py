"""A team's own documents, and moving a document between a person's own, a
team's and a project's (#3774).

A document belongs to a team — a person's own to their personal team — and to
at most one of its projects. One of no project is the team's: its members list,
read and write it (`api.doc_access`). Moving changes who reads it, so it is a
person's act — the document's author or an admin of its team — and lands only
where that person is a member.
"""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep
from app.api.doc_access import may_move_or_delete, reach
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core import rank
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
)
from app.core.sentences import say
from app.domain.block.documents import whose_document
from app.domain.living_doc import collab
from app.domain.living_doc.schemas import DocumentRowOut, document_row
from app.domain.living_doc.services import Documents
from app.domain.project.address import Numbered, take_number
from app.domain.project.services import ProjectService
from app.domain.team.services import team_service
from app.domain.user.repositories import UserRepository

teams = APIRouter(prefix="/teams", tags=["team-documents"])
documents = APIRouter(prefix="/documents", tags=["team-documents"])


async def _member(db, resolver, team_id: int):
    """The caller, when they are a member of the team; their user row too."""
    actor = await resolver.resolve()
    resolver.reject_failed_credential(actor)
    if not actor.authenticated:
        raise AuthenticationRequiredError(say("docTeamMembersOnly"))
    user = await UserRepository(db).get_by_username(actor.handle)
    if (
        actor.via == "cheese"
        or user is None
        or not await team_service(db).is_team_member(team_id, user.id)
    ):
        raise ForbiddenError(say("docTeamMembersOnly"))
    return actor, user


class TeamDocumentsOut(BaseModel):
    data: list[DocumentRowOut]
    next: str | None


@teams.get("/{team_id}/documents", **typed_response(TeamDocumentsOut))
async def team_documents(
    team_id: int, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The team's own documents — none of its projects' — the latest changed
    first. A person's own documents are their personal team's."""
    await _member(db, resolver, team_id)
    docs = await Documents(db).of_team(team_id)
    rows = [
        {**document_row(doc), "rank": rank.encode(1, doc.updated_at, str(doc.id))}
        for doc in docs
    ]
    return ok({"data": rows, "next": None})


class TeamDocumentIn(BaseModel):
    title: str = Field(default="", max_length=200)


@teams.post(
    "/{team_id}/documents",
    dependencies=[ROUTE_DECIDES],
    **typed_response(DocumentRowOut),
)
async def create_team_document(
    team_id: int, body: TeamDocumentIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """A new, empty document of the team's own."""
    actor, _ = await _member(db, resolver, team_id)
    doc = await Documents(db).create(
        project_id=None,
        team_id=team_id,
        title=body.title.strip(),
        author=actor.handle,
    )
    await db.commit()
    await db.refresh(doc)
    return ok(document_row(doc))


class PlaceProjectOut(BaseModel):
    id: str
    name: str


class PlaceOut(BaseModel):
    """Somewhere a document may move to: a team (a person's own when
    ``personal``), and the projects of it."""

    team_id: int
    #: Its address: ``/teams/<handle>``; a personal team's is its owner's username.
    handle: str
    name: str
    personal: bool
    projects: list[PlaceProjectOut]


class PlacesOut(BaseModel):
    places: list[PlaceOut]


async def _movable(db, resolver, document_id: uuid.UUID):
    """The document and its reach, once the caller may move it: a library or a
    team document — not a task's, a conversation's or the project's goals — by
    its author or an admin of its team."""
    reached = await reach(db, resolver, document_id, enforce=True)
    doc = reached.doc
    if await whose_document(db, doc) != (None, None) or (
        doc.project_id is not None and await ProjectService(db).is_overview(doc.id)
    ):
        raise ForbiddenError(say("docNotMovable"))
    if reached.actor.via == "cheese" or not await may_move_or_delete(db, reached):
        raise ForbiddenError(say("docMoveAuthorOrAdmin"))
    return reached


@documents.get("/{document_id}/places", **typed_response(PlacesOut))
async def document_places(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Where the caller may move this document: their own documents, and each
    team they are in with its projects that are not archived."""
    reached = await _movable(db, resolver, document_id)
    user = await UserRepository(db).get_by_username(reached.actor.handle)
    if user is None:
        raise ForbiddenError(say("docMoveAuthorOrAdmin"))
    teams_of = team_service(db)
    await teams_of.ensure_personal_team(user.id)
    mine = await teams_of.list_teams_of_user(user.id)
    projects = await ProjectService(db).live_of_teams([t.id for t in mine])
    by_team: dict[int, list[dict]] = {}
    for pid, name, team_id in projects:
        by_team.setdefault(team_id, []).append({"id": str(pid), "name": name})
    return ok(
        {
            "places": [
                {
                    "team_id": t.id,
                    "handle": t.handle or user.username,
                    "name": t.name,
                    "personal": t.personal_owner_user_id is not None,
                    "projects": by_team.get(t.id, []),
                }
                for t in sorted(
                    mine, key=lambda t: (t.personal_owner_user_id is None, t.name)
                )
            ]
        }
    )


class PlaceIn(BaseModel):
    team_id: int
    project_id: uuid.UUID | None = None


@documents.put(
    "/{document_id}/place",
    dependencies=[ROUTE_DECIDES],
    **typed_response(DocumentRowOut),
)
async def move_document(
    document_id: uuid.UUID, body: PlaceIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Move a document to the caller's own documents, a team of theirs, or a
    project of it. Who reads it follows where it lands; the page asked first."""
    reached = await _movable(db, resolver, document_id)
    await _member(db, resolver, body.team_id)
    doc = reached.doc
    if body.project_id is not None:
        project = await ProjectService(db).get(body.project_id)
        if project is None or project.team_id != body.team_id:
            raise NotFoundError(say("docMoveNoSuchPlace"))
        if project.archived_at is not None:
            raise UnprocessableEntityError(say("docMoveArchivedProject"))
    if doc.team_id == body.team_id and doc.project_id == body.project_id:
        return ok(document_row(doc))
    team = await team_service(db).get_team(body.team_id)
    if team is None:
        raise NotFoundError(say("docMoveNoSuchPlace"))
    doc.team_id = body.team_id
    doc.project_id = body.project_id
    # A project numbers its documents for their addresses; a team's own has none.
    doc.number = (
        await take_number(db, body.project_id, Numbered.document)
        if body.project_id is not None
        else None
    )
    doc.room_id = None
    await db.commit()
    await db.refresh(doc)
    # Whoever has it open reads it again: who may write it may have changed.
    await collab.tell(doc.id, {"type": "state", "resource": "moved"})
    return ok(document_row(doc))
