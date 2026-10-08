"""A project's own skills (项目技能), shipped to its sessions.

An AI teammate may draft or edit one; a person confirms, restores or deletes,
because what is confirmed is what every later session in the project follows.
"""

import base64
import binascii
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say, with_keys
from app.domain.agent.platform_notices import (
    EVENT_SKILL_PROPOSED,
    SEVERITY_INFO,
    WHO_CHEESE,
    notice,
)
from app.domain.agent.staleness import announce_stale
from app.domain.block.authorship import AuthorType
from app.domain.block.models import Block, BlockKind
from app.domain.identity.actor import Actor
from app.domain.membership.services import MemberService
from app.domain.memory.files import (
    MEMORY_ROOT,
    TEAM_PREFIX,
    MemoryFileError,
    MemoryFileScope,
    check_scoped_path,
    parse_index,
    parse_memory_file,
)
from app.domain.memory.files_store import MemoryFileStore
from app.domain.project_skill import importer
from app.domain.project_skill.models import ProjectSkill, ProjectSkillRevision
from app.domain.project_skill.service import ProjectSkillService
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="", tags=["project-skills"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


class SkillIn(BaseModel):
    name: str = Field(min_length=2, max_length=48)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    body: str = Field(min_length=1)
    files: dict[str, str] = Field(default_factory=dict)
    # What a teammate's proposal rests on; shown on the card a person saves it
    # from. Ignored when a person writes the method.
    taught: list[str] = Field(default_factory=list)
    accepted: str = ""
    related: str = ""
    absorbs: list[str] = Field(default_factory=list)


class SkillPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    body: str | None = None
    files: dict[str, str] | None = None
    # A teammate's edit: the correction it answers.
    reason: str = ""


_PROPOSAL_FIELDS = {"taught", "accepted", "related", "absorbs", "reason"}


async def _absorbed(
    db: AsyncSession, project_id: uuid.UUID, paths: list[str]
) -> list[dict[str, str]]:
    """The team memories a proposal folds in, each with the title a person reads
    on the card (its index line's title, else its description)."""
    store = MemoryFileStore(db)
    index = await store.index_text(project_id, MemoryFileScope.team) or ""
    titles = {entry.path: entry.title for entry in parse_index(index)}
    out: list[dict[str, str]] = []
    for raw in paths:
        path = raw.strip().removeprefix("~/").removeprefix(f"{MEMORY_ROOT}/")
        try:
            prefix, name = check_scoped_path(path)
        except MemoryFileError as exc:
            raise ValidationError(str(exc)) from exc
        if prefix != TEAM_PREFIX:
            raise ValidationError(say("skillAbsorbsTeamOnly", path=raw))
        row = await store.get(project_id, MemoryFileScope.team, None, name)
        if row is None:
            raise ValidationError(say("skillAbsorbsMissing", path=raw))
        title = titles.get(name) or parse_memory_file(row.content).description or name
        out.append({"path": path, "title": title})
    return out


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _skill(row: ProjectSkill) -> dict:
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "name": row.name,
        "title": row.title,
        "description": row.description,
        "body": row.body,
        "files": row.files,
        "state": row.state,
        "origin": row.origin,
        "shipped_revision": row.shipped_revision,
        "proposed_by": row.proposed_by,
        "confirmed_by": row.confirmed_by,
        "confirmed_at": _iso(row.confirmed_at),
        "source_topic_id": str(row.source_topic_id) if row.source_topic_id else None,
        "proposal": row.proposal,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _revision(row: ProjectSkillRevision) -> dict:
    return {
        "revision": row.revision,
        "content": row.content,
        "confirmed_by": row.confirmed_by,
        "note": row.note,
        "created_at": _iso(row.created_at),
    }


def _is_person(actor: Actor) -> bool:
    return actor.via == "token"


def _person(actor: Actor, what: str) -> None:
    if not _is_person(actor):
        raise ForbiddenError(say("personOnlyAction", what=what))


async def _in_project(
    db: AsyncSession,
    resolver: ActorResolver,
    project_id: uuid.UUID,
    topic: uuid.UUID | None,
) -> Actor:
    """A person in the project, or a teammate naming the room it speaks from."""
    if topic is not None:
        place = await TopicService(db).place_or_404(topic)
        if place.project_id != project_id:
            raise NotFoundError("Topic not found")
        actor = await resolver.resolve(project_id=project_id, topic_id=topic)
        await resolver.authorize_topic(
            actor, project_id=project_id, topic_id=topic, enforce=True
        )
        return actor
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    return actor


async def _speaker(db: AsyncSession, actor: Actor, topic: uuid.UUID | None) -> str:
    if actor.authenticated or topic is None:
        return actor.handle
    place = await TopicService(db).place_or_404(topic)
    return await TopicMemberService(db).resolve_agent_handle(place.room_id)


@router.get("/projects/{project_id}/skills")
async def list_skills(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    topic: uuid.UUID | None = None,
) -> dict:
    await _in_project(db, resolver, project_id, topic)
    items = [_skill(r) for r in await ProjectSkillService(db).list(project_id)]
    return ok(page(items, len(items)))


@router.post("/topics/{topic_id}/skills")
async def create_skill(
    topic_id: uuid.UUID, body: SkillIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    place = await TopicService(db).place_or_404(topic_id)
    actor = await _in_project(db, resolver, place.project_id, topic_id)
    by_agent = not _is_person(actor)
    by = await _speaker(db, actor, topic_id)
    service = ProjectSkillService(db)
    proposal = None
    if by_agent:
        proposal = {
            "taught": [t.strip() for t in body.taught if t.strip()],
            "accepted": body.accepted.strip(),
            "related": body.related.strip(),
            "absorbs": await _absorbed(db, place.project_id, body.absorbs),
        }
    row = await service.create(
        project_id=place.project_id,
        topic_id=place.room_id,
        by=by,
        by_agent=by_agent,
        name=body.name,
        fields=body.model_dump(exclude={"name", *_PROPOSAL_FIELDS}),
        proposal=proposal,
    )
    if by_agent:
        line = say("skillProposed", title=row.title)
        db.add(
            Block(
                id=uuid.uuid4(),
                project_id=place.project_id,
                conversation_id=place.room_id,
                author="system",
                author_type=AuthorType.platform,
                kind=BlockKind.event,
                content=line,
                # Added as a row, not through `BlockRepository.add`, so the
                # line's key is recorded here.
                meta=with_keys(
                    {
                        **notice(
                            EVENT_SKILL_PROPOSED,
                            severity=SEVERITY_INFO,
                            who=WHO_CHEESE,
                            detail=say(
                                "skillProposedDetail",
                                description=row.description,
                                body=row.body,
                            ),
                            detail_label=say("labelSkillToConfirm"),
                        ),
                        "skill_id": str(row.id),
                    },
                    content=line,
                ),
            )
        )
    await db.commit()
    await service.publish(place.project_id)
    await announce_stale(place.room_id, "skills")
    return ok(_skill(row))


class PersonSkillIn(BaseModel):
    name: str = Field(min_length=2, max_length=48)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    body: str = Field(min_length=1)
    files: dict[str, str] = Field(default_factory=dict)
    # Read from elsewhere through the import preview; a manager's to add.
    imported: bool = False


async def _person_in_project(
    db: AsyncSession, resolver: ActorResolver, project_id: uuid.UUID, what: str
) -> Actor:
    actor = await _in_project(db, resolver, project_id, None)
    _person(actor, what)
    return actor


@router.post("/projects/{project_id}/skills")
async def add_skill(
    project_id: uuid.UUID,
    body: PersonSkillIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """A person writes a skill, or adds one read through the import preview."""
    actor = await _person_in_project(db, resolver, project_id, say("skillAdd"))
    if body.imported:
        # What comes from outside reaches every later session and runs its
        # scripts on the work computer; the project's managers decide that.
        await MemberService(db).require_manager(project_id, actor)
    service = ProjectSkillService(db)
    row = await service.create(
        project_id=project_id,
        topic_id=None,
        by=actor.handle,
        by_agent=False,
        name=body.name,
        fields=body.model_dump(exclude={"name", "imported"}),
        imported=body.imported,
    )
    await db.commit()
    await service.publish(project_id)
    return ok(_skill(row))


class ImportSource(BaseModel):
    # An uploaded SKILL.md or zip, base64-encoded, or a GitHub folder's address.
    filename: str = ""
    content: str = ""
    url: str = ""


@router.post("/projects/{project_id}/skills/import-preview")
async def preview_import(
    project_id: uuid.UUID,
    body: ImportSource,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Read a skill from an uploaded SKILL.md or zip, or a GitHub folder, and
    lay it out for a manager to look over. Nothing is saved."""
    actor = await _person_in_project(db, resolver, project_id, say("skillImport"))
    await MemberService(db).require_manager(project_id, actor)
    if body.content:
        if len(body.content) > importer.MAX_TOTAL_BYTES * 4 // 3 + 4:
            raise ValidationError(say("skillImportTooLarge"))
        try:
            data = base64.b64decode(body.content, validate=True)
        except binascii.Error as exc:
            raise ValidationError(say("skillImportUnreadable")) from exc
        read = importer.read_upload(body.filename, data)
    elif body.url.strip():
        read = await importer.read_github(body.url)
    else:
        raise ValidationError(say("skillImportNothing"))
    return ok(read.to_json())


async def _changed(row: ProjectSkill) -> None:
    """Tell the room a method came from that its cards changed."""
    if row.source_topic_id is not None:
        await announce_stale(row.source_topic_id, "skills")


async def _load(
    db: AsyncSession,
    resolver: ActorResolver,
    skill_id: uuid.UUID,
    topic: uuid.UUID | None,
) -> tuple[ProjectSkill, Actor]:
    row = await ProjectSkillService(db).get(skill_id)
    actor = await _in_project(db, resolver, row.project_id, topic)
    return row, actor


@router.get("/skills/{skill_id}")
async def get_skill(
    skill_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    topic: uuid.UUID | None = None,
) -> dict:
    row, _ = await _load(db, resolver, skill_id, topic)
    service = ProjectSkillService(db)
    revisions = await service.revisions(row.id)
    return ok(
        {
            **_skill(row),
            # The files' text, which the list leaves out: a person reads or
            # edits them here.
            "contents": await service.contents(row),
            "revisions": [_revision(r) for r in revisions],
        }
    )


@router.patch("/skills/{skill_id}")
async def update_skill(
    skill_id: uuid.UUID,
    body: SkillPatch,
    db: DbSession,
    resolver: ActorResolverDep,
    topic: uuid.UUID | None = None,
) -> dict:
    row, actor = await _load(db, resolver, skill_id, topic)
    service = ProjectSkillService(db)
    by_agent = not _is_person(actor)
    row = await service.update(
        row,
        by=await _speaker(db, actor, topic),
        by_agent=by_agent,
        changes=body.model_dump(exclude_unset=True, exclude={"reason"}),
        proposal={"reason": body.reason.strip()} if by_agent else None,
    )
    await db.commit()
    await service.publish(row.project_id)
    await _changed(row)
    return ok(_skill(row))


@router.post("/skills/{skill_id}/confirm")
async def confirm_skill(
    skill_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _load(db, resolver, skill_id, None)
    _person(actor, say("skillConfirmSave"))
    service = ProjectSkillService(db)
    # The team memories the method folds in go once it is saved, so the same
    # rules are not kept in two places. Only the name part is a memory path.
    absorbed = [
        check_scoped_path(item["path"])[1]
        for item in (row.proposal or {}).get("absorbs") or []
    ]
    row = await service.confirm(row, by=actor.handle)
    if absorbed:
        await MemoryFileStore(db).forget(
            project_id=row.project_id,
            scope=MemoryFileScope.team,
            owner_handle=None,
            paths=absorbed,
            updated_by=actor.handle,
        )
    await db.commit()
    await service.publish(row.project_id)
    await _changed(row)
    return ok(_skill(row))


@router.post("/skills/{skill_id}/decline")
async def decline_skill(
    skill_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """A person turns a teammate's pending proposal down."""
    row, actor = await _load(db, resolver, skill_id, None)
    _person(actor, say("skillDecline"))
    service = ProjectSkillService(db)
    row = await service.decline(row, by=actor.handle)
    await db.commit()
    await service.publish(row.project_id)
    await _changed(row)
    return ok(_skill(row))


@router.post("/skills/{skill_id}/revisions/{revision}/restore")
async def restore_skill(
    skill_id: uuid.UUID, revision: int, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _load(db, resolver, skill_id, None)
    _person(actor, say("skillRestoreOld"))
    service = ProjectSkillService(db)
    row = await service.restore(row, revision, by=actor.handle)
    await db.commit()
    await service.publish(row.project_id)
    await _changed(row)
    return ok(_skill(row))


@router.delete("/skills/{skill_id}")
async def delete_skill(
    skill_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _load(db, resolver, skill_id, None)
    _person(actor, say("skillDelete"))
    project_id = row.project_id
    service = ProjectSkillService(db)
    await service.delete(row)
    await db.commit()
    await service.publish(project_id)
    await _changed(row)
    return ok({"deleted": str(skill_id)})
