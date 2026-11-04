"""A project's own skills (项目技能), shipped to its sessions.

An AI teammate may draft or edit one; a person confirms, restores or deletes,
because what is confirmed is what every later session in the project follows.

The row shape every skill route answers with (`_skill`) and one conversation's
slice of it (`proposals_in_conversation`) are in `app.api.project_skill_rows`: a
conversation's snapshot embeds the second and is not a route, so both live under
`app.api` where the route and the snapshot may ask for the same one (C4
`routes-independent`). This module imports the two back — the rows it returns
have to be that shape, not a second copy of it.
"""

import asyncio
import base64
import binascii
import json
import os
import re
import shutil
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.doc_access import require_role
from app.api.project_skill_rows import _iso, _skill
from app.api.request_deps import DbSession
from app.api.response import Page, ok, page, typed_response
from app.api.write_access import ROUTE_DECIDES, Can, InProject
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say, with_keys
from app.domain import skill_library as native_skills
from app.domain.agent.announce import settle_proposal
from app.domain.agent.staleness import announce_stale
from app.domain.block.models import Block, BlockKind
from app.domain.event_author import AuthorType
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
from app.domain.memory.writers import MemoryWriter
from app.domain.notice_meta import (
    EVENT_SKILL_PROPOSED,
    SEVERITY_INFO,
    WHO_HUMAN,
    notice,
)
from app.domain.project_permissions import Permission
from app.domain.project_skill import importer
from app.domain.project_skill.models import ProjectSkill, ProjectSkillRevision
from app.domain.project_skill.repo_to_skill import draft_chat, repo_to_skill
from app.domain.project_skill.service import ProjectSkillService
from app.domain.room_task.place import place_or_404
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="", tags=["project-skills"])


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


_SKILL_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


def _by_title(text: str, skills: list[ProjectSkill]) -> str:
    """``text`` with each of the project's skill ids it names replaced by that
    skill's title.

    A teammate knows the project's skills by the id at the end of each one
    (it is what it passes to `cheese_skill(action=update)`), so the closest skill it
    names on a proposal card arrives as an id; the person deciding reads
    titles.
    """
    titles = {str(skill.id): skill.title for skill in skills}
    return _SKILL_ID.sub(
        lambda m: (
            f"「{titles[m.group(0).lower()]}」"
            if m.group(0).lower() in titles
            else m.group(0)
        ),
        text,
    )


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
        place = await place_or_404(db, topic)
        if place.project_id != project_id:
            raise NotFoundError(say("topicNotFound"))
        actor = await resolver.resolve(project_id=project_id, topic_id=topic)
        await resolver.authorize_topic(
            actor, project_id=project_id, topic_id=topic, enforce=True
        )
        return actor
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(
        actor, project_id=project_id, permission=Permission.configure_agents
    )
    return actor


async def _speaker(db: AsyncSession, actor: Actor, topic: uuid.UUID | None) -> str:
    if actor.authenticated or topic is None:
        return actor.handle
    place = await place_or_404(db, topic)
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


def _post_line(
    db: AsyncSession,
    row: ProjectSkill,
    conversation_id: uuid.UUID,
    line: str,
    detail: str,
    *,
    edit: bool = False,
) -> None:
    """The conversation's line about a teammate's proposal: what it is, and a
    way to the card a person saves it from."""
    db.add(
        Block(
            id=uuid.uuid4(),
            project_id=row.project_id,
            conversation_id=conversation_id,
            author="system",
            author_type=AuthorType.platform,
            kind=BlockKind.event,
            content=line,
            # Added as a row, not through `BlockRepository.add`, so the line's
            # key is recorded here.
            meta=with_keys(
                {
                    **notice(
                        EVENT_SKILL_PROPOSED,
                        severity=SEVERITY_INFO,
                        # It waits on a person saving or declining it.
                        who=WHO_HUMAN,
                        detail=detail,
                        detail_label=say("labelSkillToConfirm"),
                    ),
                    "skill_id": str(row.id),
                    **({"edit": True} if edit else {}),
                },
                content=line,
            ),
        )
    )


async def _has_open_line(
    db: AsyncSession, conversation_id: uuid.UUID, skill_id: uuid.UUID
) -> bool:
    """Whether the conversation already has a line about this skill nobody
    decided on."""
    rows = await db.scalars(
        select(Block.meta).where(
            Block.conversation_id == conversation_id,
            Block.kind == BlockKind.event,
            Block.meta["event_type"].as_string() == EVENT_SKILL_PROPOSED,
            Block.meta["skill_id"].as_string() == str(skill_id),
        )
    )
    return any((meta or {}).get("state") != "over" for meta in rows)


@router.post("/topics/{topic_id}/skills", dependencies=[ROUTE_DECIDES])
async def create_skill(
    topic_id: uuid.UUID, body: SkillIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    place = await place_or_404(db, topic_id)
    actor = await _in_project(db, resolver, place.project_id, topic_id)
    await require_role(db, actor, place.project_id, Permission.configure_agents)
    by_agent = not _is_person(actor)
    by = await _speaker(db, actor, topic_id)
    service = ProjectSkillService(db)
    proposal = None
    if by_agent:
        proposal = {
            "taught": [t.strip() for t in body.taught if t.strip()],
            "accepted": body.accepted.strip(),
            "related": _by_title(
                body.related.strip(),
                await service.list(place.project_id),
            ),
            "absorbs": await _absorbed(db, place.project_id, body.absorbs),
        }
    row = await service.create(
        project_id=place.project_id,
        # The conversation the teammate proposed from, not the room it hangs
        # in: the card and the line below are drawn there, as the other cards a
        # teammate raises are.
        topic_id=place.conversation_id,
        by=by,
        by_agent=by_agent,
        name=body.name,
        fields=body.model_dump(exclude={"name", *_PROPOSAL_FIELDS}),
        proposal=proposal,
    )
    if by_agent:
        _post_line(
            db,
            row,
            place.conversation_id,
            say("skillProposedBy", who=f"<@{by}>", title=row.title),
            say("skillProposedDetail", description=row.description, body=row.body),
        )
    await db.commit()
    await service.publish(place.project_id)
    await announce_stale(place.conversation_id, "skills")
    return ok(_skill(row))


class PersonSkillIn(BaseModel):
    name: str = Field(min_length=2, max_length=48)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    body: str = Field(min_length=1)
    files: dict[str, str] = Field(default_factory=dict)
    # Read from elsewhere through the import preview; a manager's to add.
    imported: bool = False


@router.post("/projects/{project_id}/skills")
async def add_skill(
    at: Annotated[InProject, Can(Permission.configure_agents)],
    body: PersonSkillIn,
    db: DbSession,
) -> dict:
    """A person writes a skill, or adds one read through the import preview."""
    project_id, actor = at.project.id, at.actor
    _person(actor, say("skillAdd"))
    if body.imported:
        # What comes from outside reaches every later session and runs its
        # scripts on the work computer; the project's managers decide that.
        await MemberService(db).require(project_id, actor, Permission.edit_settings)
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
    at: Annotated[InProject, Can(Permission.configure_agents)],
    body: ImportSource,
    db: DbSession,
) -> dict:
    """Read a skill from an uploaded SKILL.md or zip, or a GitHub address, and lay
    it out for a manager to look over. Nothing is saved.

    A GitHub folder that already holds a ``SKILL.md`` is read as it is; any other
    repository is turned into a draft from its README and entry files
    (``project_skill/repo_to_skill.py``). Either way the shape is ``ReadSkill``,
    so 「预览 → 保存」 downstream does not know the difference.
    """
    _person(at.actor, say("skillImport"))
    await MemberService(db).require(at.project.id, at.actor, Permission.edit_settings)
    if body.content:
        if len(body.content) > importer.MAX_TOTAL_BYTES * 4 // 3 + 4:
            raise ValidationError(say("skillImportTooLarge"))
        try:
            data = base64.b64decode(body.content, validate=True)
        except binascii.Error as exc:
            raise ValidationError(say("skillImportUnreadable")) from exc
        read = importer.read_upload(body.filename, data)
    elif body.url.strip():
        read = await repo_to_skill(body.url, lambda: draft_chat(db))
    else:
        raise ValidationError(say("skillImportNothing"))
    return ok(read.to_json())


async def _settled(db: AsyncSession, row: ProjectSkill, *, saved: bool) -> None:
    """A person decided on a teammate's proposal: its line in that conversation
    says so."""
    if row.source_topic_id is None:
        return
    await settle_proposal(
        db,
        row.source_topic_id,
        event_type=EVENT_SKILL_PROPOSED,
        key="skill_id",
        value=row.id,
        content=lambda meta: say(
            ("skillEditSavedBy" if saved else "skillEditNotSavedBy")
            if meta.get("edit")
            else ("skillProposalSavedBy" if saved else "skillProposalNotSavedBy"),
            who=f"<@{row.proposed_by}>",
            title=row.title,
        ),
    )


async def _changed(row: ProjectSkill) -> None:
    """Tell the conversation a method came from that its cards changed."""
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


@router.patch("/skills/{skill_id}", dependencies=[ROUTE_DECIDES])
async def update_skill(
    skill_id: uuid.UUID,
    body: SkillPatch,
    db: DbSession,
    resolver: ActorResolverDep,
    topic: uuid.UUID | None = None,
) -> dict:
    row, actor = await _load(db, resolver, skill_id, topic)
    await require_role(db, actor, row.project_id, Permission.configure_agents)
    service = ProjectSkillService(db)
    by_agent = not _is_person(actor)
    pending = row.state == "draft"
    if not by_agent:
        # Rewriting a method changes what every later session follows, and the
        # method may be another member's -- a manager decides that. A teammate's
        # edit stays a proposal for a person to confirm instead (`by_agent`).
        await MemberService(db).require_manager(row.project_id, actor)
    row = await service.update(
        row,
        by=await _speaker(db, actor, topic),
        by_agent=by_agent,
        changes=body.model_dump(exclude_unset=True, exclude={"reason"}),
        proposal={"reason": body.reason.strip()} if by_agent else None,
    )
    if by_agent and topic is not None:
        # The card goes where the teammate was corrected: the person who just
        # corrected it decides there, as with a new skill.
        conversation_id = (await place_or_404(db, topic)).conversation_id
        row.source_topic_id = conversation_id
        if not await _has_open_line(db, conversation_id, row.id):
            edit = row.shipped_revision > 0
            _post_line(
                db,
                row,
                conversation_id,
                say(
                    "skillEditProposedBy" if edit else "skillProposedBy",
                    who=f"<@{row.proposed_by}>",
                    title=row.title,
                ),
                say(
                    "skillEditProposedDetail", reason=body.reason.strip(), body=row.body
                )
                if edit
                else say(
                    "skillProposedDetail", description=row.description, body=row.body
                ),
                edit=edit,
            )
    if pending and not by_agent:
        # A person's own edit is what gets saved; the teammate's wording is not.
        await _settled(db, row, saved=False)
    await db.commit()
    await service.publish(row.project_id)
    await _changed(row)
    return ok(_skill(row))


@router.post("/skills/{skill_id}/confirm", dependencies=[ROUTE_DECIDES])
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
    await _settled(db, row, saved=True)
    if absorbed:
        await MemoryFileStore(db).forget(
            project_id=row.project_id,
            scope=MemoryFileScope.team,
            owner_handle=None,
            paths=absorbed,
            updated_by=actor.handle,
            writer=MemoryWriter.skill,
        )
    await db.commit()
    await service.publish(row.project_id)
    await _changed(row)
    return ok(_skill(row))


@router.post("/skills/{skill_id}/decline", dependencies=[ROUTE_DECIDES])
async def decline_skill(
    skill_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """A person turns a teammate's pending proposal down."""
    row, actor = await _load(db, resolver, skill_id, None)
    _person(actor, say("skillDecline", agent=f"<@{row.proposed_by}>"))
    service = ProjectSkillService(db)
    row = await service.decline(row, by=actor.handle)
    await _settled(db, row, saved=False)
    await db.commit()
    await service.publish(row.project_id)
    await _changed(row)
    return ok(_skill(row))


@router.post(
    "/skills/{skill_id}/revisions/{revision}/restore", dependencies=[ROUTE_DECIDES]
)
async def restore_skill(
    skill_id: uuid.UUID, revision: int, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _load(db, resolver, skill_id, None)
    _person(actor, say("skillRestoreOld"))
    service = ProjectSkillService(db)
    pending = row.state == "draft"
    row = await service.restore(row, revision, by=actor.handle)
    if pending:
        await _settled(db, row, saved=False)
    await db.commit()
    await service.publish(row.project_id)
    await _changed(row)
    return ok(_skill(row))


@router.delete("/skills/{skill_id}", dependencies=[ROUTE_DECIDES])
async def delete_skill(
    skill_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    row, actor = await _load(db, resolver, skill_id, None)
    _person(actor, say("skillDelete"))
    # Deleting takes the method away from every later session, and it may be
    # another member's; a manager decides that.
    await MemberService(db).require_manager(row.project_id, actor)
    project_id = row.project_id
    service = ProjectSkillService(db)
    if row.state == "draft":
        await _settled(db, row, saved=False)
    await service.delete(row)
    await db.commit()
    await service.publish(project_id)
    await _changed(row)
    return ok({"deleted": str(skill_id)})


# --- The skill panel: what a teammate carries, and what a room can switch on ---
#
# Two shapes of the same catalog. The shipped skills (`sandbox/skills`) already
# reach every executor, so a name here is only a promise that the turn's prompt
# will carry that skill's body (`agent/turn/intake/preparation.py`). A project's
# settings edit ONE teammate's `agent_instances.configuration.skills`
# (`/projects/{id}/skill-catalog`, below); the room-level switch (`topics.skills`)
# is the older fallback a prompt falls back to when the teammate carries nothing.


class TopicSkillsIn(BaseModel):
    enabled: list[str] = Field(default_factory=list)


class SkillCatalogItem(BaseModel):
    """A shipped skill the platform can put in a prompt: its name and what it
    does. `enabled` only exists on a room's list — a project's catalog is the
    same items without the per-room switch."""

    name: str
    description: str


class TopicSkillItem(SkillCatalogItem):
    enabled: bool


class SkillCatalogOut(Page[SkillCatalogItem]):
    """A project's catalog of shipped skills."""


class TopicSkillsCatalogOut(Page[TopicSkillItem]):
    """A room's catalog: the same items, each with its own switch."""


class TopicSkillsOut(BaseModel):
    """What a room switched on, after unknown names were dropped."""

    enabled: list[str]


class SkillHealthOut(BaseModel):
    """Whether a skill can run right now, and why not when it cannot."""

    status: str
    detail: str


def _platform_catalog() -> list[dict]:
    """The shipped skills whose guidance can actually be put in a prompt — a name
    with no `SKILL.md` on disk (`cheese-docs`, built by composition) is left out:
    a socket that lights up and does nothing is worse than no socket."""
    out: list[dict] = []
    for name in native_skills.shipped_skill_names():
        doc = native_skills.native_skill_doc(name)
        if doc is None:
            continue
        out.append({"name": name, "description": doc[0]})
    return out


@router.get("/topics/{topic_id}/skills", **typed_response(TopicSkillsCatalogOut))
async def list_topic_skills(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    place = await place_or_404(db, topic_id)
    await _in_project(db, resolver, place.project_id, topic_id)
    enabled = set(place.room.skills or [])
    items = [{**s, "enabled": s["name"] in enabled} for s in _platform_catalog()]
    return ok(page(items, len(items)))


@router.get("/projects/{project_id}/skill-catalog", **typed_response(SkillCatalogOut))
async def project_skill_catalog(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The shipped skills a teammate in this project can be armed with. Project,
    not room: which teammate carries what is a project setting (and only a
    manager may change it — `projects_agents.py`)."""
    await _in_project(db, resolver, project_id, None)
    items = _platform_catalog()
    return ok(page(items, len(items)))


@router.get("/skill-catalog", **typed_response(SkillCatalogOut))
async def skill_catalog(_: AuthUserInfo = Depends(require_auth_user)) -> dict:
    """The shipped skills, for a 题目's publisher picking the ones every
    teammate of its projects carries (`Teaching.skills`). It names no project:
    the catalog is the platform's and the same for everyone, and a 题目 being
    written has no project yet — signing in is the whole gate."""
    items = _platform_catalog()
    return ok(page(items, len(items)))


@router.put(
    "/topics/{topic_id}/skills",
    dependencies=[ROUTE_DECIDES],
    **typed_response(TopicSkillsOut),
)
async def set_topic_skills(
    topic_id: uuid.UUID,
    body: TopicSkillsIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    place = await place_or_404(db, topic_id)
    await _in_project(db, resolver, place.project_id, topic_id)
    # Only names the platform can actually hand this room: an unknown one would
    # ride the prompt as a promise nothing backs (`native_skill_bodies`).
    known = {s["name"] for s in _platform_catalog()}
    enabled = list(dict.fromkeys(n for n in body.enabled if n in known))
    place.room.skills = enabled or None
    await db.commit()
    return ok({"enabled": enabled})


# --- Is this skill runnable right now? ----------------------------------------
#
# A switch that says "on" while the tool it names cannot run is worse than no
# switch. Only Wolfram has a real probe (`scripts/wolfram_mcp.py health`, which
# reaches the official MCP); the rest answer by their files being present, which
# is all "loadable" means for a prompt-only skill.

_HEALTH_TTL_S = 300
_health_cache: dict[str, tuple[float, dict]] = {}


async def _probe_wolfram() -> dict:
    # ponytail: 探针在 backend 主机上起一个子进程跑技能自带脚本（上限：宿主要有
    # uv 且能连到 MCP；探不到就如实说 unavailable/degraded，绝不假装可用）。
    root = native_skills.native_skill_dir("wolfram")
    script = root / "scripts" / "wolfram_mcp.py" if root is not None else None
    if script is None or not script.is_file():
        return {"status": "unavailable", "detail": "技能文件缺失"}
    if shutil.which("uv") is None:
        return {"status": "unavailable", "detail": "未安装 uv"}
    proc = await asyncio.create_subprocess_exec(
        "uv",
        "run",
        "--quiet",
        "--script",
        str(script),
        "health",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        # 探针跑的是装船目录里的脚本：别让它在平台的技能树里甩下 __pycache__。
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=25)
    except TimeoutError:
        proc.kill()
        return {"status": "degraded", "detail": "探针超时"}
    if proc.returncode != 0:
        return {"status": "degraded", "detail": "探针执行失败"}
    try:
        payload = json.loads(out.decode() or "{}")
    except ValueError:
        return {"status": "degraded", "detail": "探针输出无法解析"}
    reachable = bool((payload.get("wolfram") or {}).get("available"))
    return {
        "status": "ok" if reachable else "degraded",
        "detail": "Wolfram 通道可用" if reachable else "Wolfram 通道不可达",
    }


@router.get("/topics/{topic_id}/skills/{name}/health", **typed_response(SkillHealthOut))
async def skill_health(
    topic_id: uuid.UUID, name: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    place = await place_or_404(db, topic_id)
    await _in_project(db, resolver, place.project_id, topic_id)
    return await _skill_health(name)


@router.get(
    "/projects/{project_id}/skills/{name}/health", **typed_response(SkillHealthOut)
)
async def project_skill_health(
    project_id: uuid.UUID, name: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The same probe, asked from a project's settings instead of a room."""
    await _in_project(db, resolver, project_id, None)
    return await _skill_health(name)


async def _skill_health(name: str) -> dict:
    if name not in {s["name"] for s in _platform_catalog()}:
        raise NotFoundError(say("skillUnknown"))
    now = time.monotonic()
    cached = _health_cache.get(name)
    if cached is not None and now - cached[0] < _HEALTH_TTL_S:
        return ok(cached[1])
    result = (
        await _probe_wolfram() if name == "wolfram" else {"status": "ok", "detail": ""}
    )
    _health_cache[name] = (now, result)
    return ok(result)
