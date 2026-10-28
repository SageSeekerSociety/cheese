"""Find what the project already knows, from where the caller stands.

One query across the rooms the caller may read: room titles, messages and
documents, weekly reports, tasks, library file names and the artifact list. Every
word of the query has to be found; each group lists its best matches first (see
`app.domain.search.bm25`), library file names aside, which are matched as
written. `limit` caps each group, and each kind of record (message, weekly…)
within its group. Each hit names where it lives, so it can be cited; rooms the
caller may not read are not searched and are only counted.

`only` narrows the search to some groups and pages through them with `offset`:
the search results page reads one kind at a time, a page at a time, and
`with_counts` adds how many of each kind there are.
"""

import uuid
from collections.abc import Iterable
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.project_library import shelf_for
from app.api.request_deps import DbSession
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import AuthenticationRequiredError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.agent_instance.adapters import project_team
from app.domain.assistant import refs
from app.domain.block.models import Block, BlockKind
from app.domain.conversation.services import rooms_of_inner
from app.domain.identity.actor import Actor
from app.domain.library import listing as library_listing
from app.domain.library.shelf import Shelf
from app.domain.living_doc import search as doc_search
from app.domain.living_doc.schemas import document_row
from app.domain.membership.roster import roster
from app.domain.project import address
from app.domain.project.models import Project, ProjectArtifact
from app.domain.project.services import ProjectService
from app.domain.room_task.models import Task
from app.domain.room_task.place import place_or_404
from app.domain.search import bm25
from app.domain.topic.models import Topic
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.services import faces_by_handle

router = APIRouter(prefix="", tags=["project-context"])


SEARCHED_BLOCKS = (
    BlockKind.message,
    BlockKind.weekly,
)

#: Every kind of record, in the order the search lists them: blocks, and the
#: kinds of document hit (`app.domain.living_doc.search`).
RECORD_KINDS = ("message", *doc_search.KINDS, "weekly")


# Written out rather than bound: the blocks index is partial on these kinds and
# a few others (migration 2d2fc3a8ce36), and Postgres only uses it when it can
# prove the query's predicate implies the index's — which it cannot do against
# bind parameters once a prepared statement goes generic.
_SEARCHED_BLOCKS_SQL = text(
    "blocks.kind IN (" + ", ".join(f"'{k.value}'" for k in SEARCHED_BLOCKS) + ")"
)


#: What `only` may name: a kind of record, or one of the two other groups.
ONLY_VALUES = set(RECORD_KINDS) | {"tasks", "library"}


def _snippet(body: str, terms: list[str], width: int = 160) -> str:
    body = " ".join((body or "").split())
    lowered = body.lower()
    found = [at for at in (lowered.find(t.lower()) for t in terms) if at >= 0]
    at = min(found) if found else 0
    start = max(at - width // 2, 0)
    piece = body[start : start + width]
    return ("…" if start else "") + piece + ("…" if start + width < len(body) else "")


def _blocks_matching(
    terms: list[str], conversations: Iterable[uuid.UUID], kinds: list[str]
) -> ColumnElement[bool]:
    return bm25.match_all_words(
        Block.id,
        terms,
        {"content": 1},
        filters=[
            bm25.any_of("conversation_id", conversations),
            bm25.any_of("kind", kinds),
        ],
    )


async def _conversation_rooms(
    db: AsyncSession, rooms: list[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """Each conversation of these rooms — the rooms' own, their tasks' and their
    支线' — mapped to its room."""
    return await rooms_of_inner(db, rooms)


def _tasks_matching(
    terms: list[str], in_readable: list[uuid.UUID]
) -> ColumnElement[bool]:
    return bm25.match_all_words(
        Task.id,
        terms,
        {"title": 2, "conclusion": 1},
        filters=[bm25.any_of("room_id", in_readable)],
    )


async def _readable_rooms(
    db: AsyncSession,
    resolver: ActorResolver,
    project_id: uuid.UUID,
    topic: uuid.UUID | None,
) -> tuple[dict[uuid.UUID, Topic], int, Actor]:
    """The rooms this caller may read, how many others were left out, and who the
    caller is (their library shelf follows from it, `project_library`)."""
    if topic is not None:
        place = await place_or_404(db, topic)
        if place.project_id != project_id:
            raise NotFoundError(say("topicNotFound"))
        actor = await resolver.resolve(project_id=project_id, topic_id=topic)
        if resolver.turn_conversation(actor) is not None:
            # An agent's results end up in that room: the same rule as any of
            # its project-wide reads.
            await resolver.authorize_reading_from(
                actor, project_id=project_id, conversation_id=topic
            )
        else:
            await resolver.authorize_topic(
                actor, project_id=project_id, topic_id=topic, enforce=True
            )
        if not actor.authenticated:
            # The development credential speaks as the room's own teammate.
            seat = await TopicMemberService(db).resolve_agent_handle(place.room_id)
            actor = Actor(handle=seat, user_id=None, via="cheese")
    else:
        actor = await resolver.resolve(project_id=project_id)
        await resolver.authorize_project(actor, project_id=project_id)

    rooms = list(await db.scalars(select(Topic).where(Topic.project_id == project_id)))
    ids = await resolver.readable_topic_ids(actor, project_id=project_id, topics=rooms)
    readable = {room.id: room for room in rooms if room.id in ids}
    return readable, len(rooms) - len(readable), actor


def _query(q: str) -> str:
    q = q.strip()
    if not q:
        raise ValidationError(say("searchQueryEmpty"))
    return q


def _only(only: list[str] | None) -> set[str] | None:
    if only is None:
        return None
    unknown = set(only) - ONLY_VALUES
    if unknown:
        raise ValidationError(
            say("searchCategoriesUnknown", categories=", ".join(sorted(unknown)))
        )
    return set(only)


@router.get("/projects/{project_id}/context/search")
async def search_project_context(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    topic: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    only: Annotated[list[str] | None, Query()] = None,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    with_counts: bool = False,
) -> dict:
    q = _query(q)
    groups = _only(only)
    readable, skipped, actor = await _readable_rooms(db, resolver, project_id, topic)
    shelf = await shelf_for(db, project_id)
    terms = bm25.words(q)
    await bm25.serial_scans(db)
    hits = (
        await search_everything(db, project_id, q, terms, readable, limit, shelf)
        if groups is None
        else await _page(
            db, project_id, q, terms, readable, groups, limit, offset, shelf
        )
    )
    body: dict = {
        "query": q,
        "searched_rooms": len(readable),
        "skipped_rooms": skipped,
        "hits": hits,
        "total": sum(len(v) for v in hits.values()),
    }
    if with_counts:
        # The results page asks for its first page and the per-kind counts
        # together: one room check instead of two.
        body["counts"] = await _counts(db, project_id, q, terms, list(readable), shelf)
    return ok(body)


async def _page(
    db: AsyncSession,
    project_id: uuid.UUID,
    q: str,
    terms: list[str],
    readable: dict[uuid.UUID, Topic],
    groups: set[str],
    limit: int,
    offset: int,
    shelf: Shelf,
) -> dict[str, list[dict]]:
    hits: dict[str, list[dict]] = {"records": [], "tasks": [], "library": []}
    in_readable = list(readable)
    kinds = [k for k in RECORD_KINDS if k in groups]
    # One ranked list across the kinds asked for, not a slice of each: a page
    # of 文档 is the best document paragraphs and comments together. Each
    # source gives its best `offset + limit`, and the page is cut from them
    # merged by score.
    if kinds and in_readable:
        ranked: list[tuple[float, dict]] = []
        block_kinds = [k for k in kinds if k not in doc_search.KINDS]
        by_conversation = await _conversation_rooms(db, in_readable)
        if block_kinds:
            score = func.paradedb.score(Block.id)
            rows = await db.execute(
                select(Block, score)
                .where(
                    _blocks_matching(terms, by_conversation, block_kinds),
                    _SEARCHED_BLOCKS_SQL,
                )
                .order_by(score.desc(), Block.id)
                .limit(offset + limit)
            )
            ranked += [
                (float(value or 0), _record(b, by_conversation, readable, terms))
                for b, value in rows
            ]
        doc_homes = await doc_search.homes(db, in_readable)
        docs = await doc_search.readable(db, in_readable)
        for kind in (k for k in kinds if k in doc_search.KINDS):
            found = await doc_search.find(db, kind, terms, docs, limit=offset + limit)
            ranked += [
                (h.score, _doc_record(h, doc_homes, readable, terms)) for h in found
            ]
        ranked.sort(key=lambda pair: -pair[0])
        hits["records"] = [record for _, record in ranked[offset : offset + limit]]
        await _name_authors(db, project_id, hits["records"])
    if "tasks" in groups and in_readable:
        tasks = await db.scalars(
            select(Task)
            .where(_tasks_matching(terms, in_readable))
            .order_by(func.paradedb.score(Task.id).desc(), Task.id)
            .offset(offset)
            .limit(limit)
        )
        hits["tasks"] = [_task(t, readable, terms) for t in tasks]
    if "library" in groups:
        hits["library"] = await library_listing.matching(
            db, shelf, q, limit=limit, offset=offset
        )
    return hits


async def _counts(
    db: AsyncSession,
    project_id: uuid.UUID,
    q: str,
    terms: list[str],
    in_readable: list[uuid.UUID],
    shelf: Shelf,
) -> dict[str, int]:
    """How many of each kind the search finds, named as `only` names them."""
    counts: dict[str, int] = dict.fromkeys(ONLY_VALUES, 0)
    if in_readable:
        by_conversation = await _conversation_rooms(db, in_readable)
        rows = await db.execute(
            select(Block.kind, func.count())
            .where(
                _blocks_matching(
                    terms, by_conversation, [k.value for k in SEARCHED_BLOCKS]
                ),
                _SEARCHED_BLOCKS_SQL,
            )
            .group_by(Block.kind)
        )
        for kind, n in rows:
            counts[str(kind.value)] = n
        docs = await doc_search.readable(db, in_readable)
        for doc_kind in doc_search.KINDS:
            counts[doc_kind] = await doc_search.count(db, doc_kind, terms, docs)
        counts["tasks"] = (
            await db.scalar(
                select(func.count())
                .select_from(Task)
                .where(_tasks_matching(terms, in_readable))
            )
            or 0
        )
    counts["library"] = await library_listing.count_matching(db, shelf, q)
    return counts


def _record(
    b: Block,
    by_conversation: dict[uuid.UUID, uuid.UUID],
    readable: dict[uuid.UUID, Topic],
    terms: list[str],
) -> dict:
    room = readable[by_conversation[b.conversation_id]]
    return {
        "room_id": str(room.id),
        "room_title": room.title,
        "id": str(b.id),
        "kind": str(b.kind.value),
        "author": b.author,
        "created_at": b.created_at.isoformat(),
        "task_id": str(b.conversation_id) if b.conversation_id != room.id else None,
        "snippet": _snippet(b.content, terms),
    }


def _doc_record(
    hit: doc_search.Hit,
    homes: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID | None]],
    readable: dict[uuid.UUID, Topic],
    terms: list[str],
) -> dict:
    room_id, task_id = homes[hit.document.id]
    room = readable[room_id]
    return {
        "room_id": str(room.id),
        "room_title": room.title,
        "id": str(hit.id),
        "kind": hit.kind,
        "author": hit.author,
        "created_at": hit.created_at.isoformat(),
        "task_id": str(task_id) if task_id else None,
        "snippet": _snippet(hit.content, terms),
    }


async def _name_authors(
    db: AsyncSession, project_id: uuid.UUID, records: list[dict]
) -> None:
    """Give each record the name its author goes by now, beside the handle it
    stores: a teammate of this project by the name the roster gives it, with
    ``author_name_source`` (``default`` while it keeps the name it was born
    with, which a screen shows in its reader's language), anyone else by their
    nickname. ``author_name`` is None when there is none; the page then shows
    the handle."""
    authors = {r["author"] for r in records}
    if not authors:
        return
    # A teammate's account keeps the nickname it was created with, so its
    # current name comes from the roster, under either of its handles.
    teammates: dict[str, tuple[str, str | None]] = {}
    for member in await roster(db, project_id, team=project_team):
        if member.instance_handle is not None:
            named = (member.name, member.name_source)
            teammates[member.handle] = named
            teammates[member.instance_handle] = named
    faces = await faces_by_handle(db, authors - teammates.keys())
    for record in records:
        name, source = teammates.get(record["author"]) or (
            faces.get(record["author"], (None, None))[0],
            None,
        )
        record["author_name"] = name
        record["author_name_source"] = source


def _task(t: Task, readable: dict[uuid.UUID, Topic], terms: list[str]) -> dict:
    room = readable[t.room_id]
    return {
        "room_id": str(room.id),
        "room_title": room.title,
        "id": str(t.id),
        "title": t.title,
        "title_source": str(t.title_source),
        "status": str(t.status.value),
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        "snippet": _snippet(" ".join(filter(None, (t.title, t.conclusion))), terms),
    }


async def search_everything(
    db: AsyncSession,
    project_id: uuid.UUID,
    q: str,
    terms: list[str],
    readable: dict[uuid.UUID, Topic],
    limit: int,
    shelf: Shelf,
) -> dict[str, list[dict]]:
    """``shelf`` is the project's library as the caller reads it
    (`project_library.shelf_for`)."""

    def where(room_id: uuid.UUID) -> dict:
        room = readable[room_id]
        return {
            "room_id": str(room.id),
            "room_title": room.title,
        }

    hits: dict[str, list[dict]] = {"rooms": [], "records": [], "tasks": []}
    if readable:
        in_readable = list(readable)
        found_rooms = await db.scalars(
            select(Topic)
            .where(
                bm25.match_all_words(
                    Topic.id,
                    terms,
                    {"title": 1},
                    filters=[bm25.any_of("id", in_readable)],
                )
            )
            .order_by(func.paradedb.score(Topic.id).desc(), Topic.id)
            .limit(limit)
        )
        hits["rooms"] = [
            {**where(r.id), "status": str(r.status.value)} for r in found_rooms
        ]
        # Each kind gets its own `limit`: a busy conversation would otherwise
        # fill every slot, and the decision or document paragraph that also
        # matches would never be listed.
        doc_homes = await doc_search.homes(db, in_readable)
        docs = await doc_search.readable(db, in_readable)
        by_conversation = await _conversation_rooms(db, in_readable)
        records: list[dict] = []
        for kind in RECORD_KINDS:
            if kind in doc_search.KINDS:
                found = await doc_search.find(db, kind, terms, docs, limit=limit)
                records += [_doc_record(h, doc_homes, readable, terms) for h in found]
                continue
            records += [
                _record(b, by_conversation, readable, terms)
                for b in await db.scalars(
                    select(Block)
                    .where(
                        _blocks_matching(terms, by_conversation, [kind]),
                        _SEARCHED_BLOCKS_SQL,
                    )
                    .order_by(func.paradedb.score(Block.id).desc(), Block.id)
                    .limit(limit)
                )
            ]
        await _name_authors(db, project_id, records)
        hits["records"] = records
        tasks = await db.scalars(
            select(Task)
            .where(_tasks_matching(terms, in_readable))
            .order_by(func.paradedb.score(Task.id).desc(), Task.id)
            .limit(limit)
        )
        hits["tasks"] = [_task(t, readable, terms) for t in tasks]

    hits["library"] = await library_listing.matching(db, shelf, q, limit=limit)
    artifacts = await db.scalars(
        select(ProjectArtifact)
        .where(
            bm25.match_all_words(
                ProjectArtifact.id,
                terms,
                {"name": 2, "about": 1},
                filters=[bm25.any_of("project_id", [project_id])],
            )
        )
        .order_by(func.paradedb.score(ProjectArtifact.id).desc(), ProjectArtifact.id)
        .limit(limit)
    )
    hits["artifacts"] = [
        {"id": str(a.id), "name": a.name, "about": a.about} for a in artifacts
    ]
    return hits


@router.get("/projects/{project_id}/documents/search")
async def search_documents(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
    """The library's search: the project's own documents by title and by what
    they say, and the living documents of the channels the caller may read —
    tasks' documents, the project's goals — by what they say. Those are not
    in the library's list, but they are found here, under their channel and
    task, to open there or keep a copy of."""
    q = _query(q)
    readable, _, _ = await _readable_rooms(db, resolver, project_id, None)
    terms = bm25.words(q)
    await bm25.serial_scans(db)
    own = await doc_search.own(db, project_id)
    homes = await doc_search.homes(db, list(readable))
    rooms = await doc_search.readable(db, list(readable))
    written = {i: d for i, d in own.items() if d.version > 0}
    found = await doc_search.find(db, "doc", terms, {**written, **rooms}, limit=limit)
    by_title = [
        d
        for d in sorted(own.values(), key=lambda d: d.updated_at, reverse=True)
        if q.lower() in (d.title or "").lower()
    ]
    library_hits: list[dict] = []
    seen: set[uuid.UUID] = set()
    for doc in [*by_title, *(h.document for h in found if h.document.id not in homes)]:
        if doc.id in seen or len(library_hits) >= limit:
            continue
        seen.add(doc.id)
        library_hits.append(
            {**document_row(doc), "snippet": _snippet(doc.content, terms)}
        )
    # A task's document goes by its task's title, the project's goals by 综合's.
    task_titles: dict[uuid.UUID, str] = {
        task_id: title
        for task_id, title in await db.execute(
            select(Task.id, Task.title).where(
                Task.id.in_([task for _, task in homes.values() if task])
            )
        )
    }
    room_hits = [
        {
            **document_row(h.document),
            "room_id": str(room),
            "task_id": str(task) if task else None,
            "room_title": task_titles.get(task) if task else readable[room].title,
            "snippet": _snippet(h.content, terms),
        }
        for h in found
        if h.document.id in homes
        for room, task in [homes[h.document.id]]
    ]
    return ok({"query": q, "library": library_hits, "rooms": room_hits})


#: How many of the person's projects a search without a project looks in, the
#: latest first, and how many hits of each kind it lists per project.
AGENT_PROJECTS = 8
AGENT_HITS = 5
#: What a record's kind is called where 芝士 reads it.
_RECORD_NAMES = {"message": "消息", "weekly": "周报"}


class AgentSearchIn(BaseModel):
    query: str
    #: A project's slug; without one, every project the person is in.
    project: str | None = None


async def _agent_hits(
    db: AsyncSession, project: Project, hits: dict[str, list[dict]], readable
) -> list[str]:
    """One project's hits in 芝士's words, each with the ref that reads it."""
    slug = project.slug
    lines: list[str] = []
    numbers: dict[str, int | None] = {}

    async def task_ref(task_id: str) -> str:
        if task_id not in numbers:
            found = await address.thing_by_id(
                db, address.Numbered.task, uuid.UUID(task_id)
            )
            numbers[task_id] = found.number if found else None
        return refs.task(slug, numbers[task_id])

    def room_ref(room_id: str) -> str:
        return refs.channel(slug, readable[uuid.UUID(room_id)].number)

    for room in hits.get("rooms", []):
        lines.append(f"- 频道「{room['room_title']}」（{room_ref(room['room_id'])}）")
    for task in hits.get("tasks", []):
        state = "进行中" if task["status"] == "open" else "已结束"
        lines.append(
            f"- 任务「{task['title']}」（{await task_ref(task['id'])}），{state}："
            f"{task['snippet']}"
        )
    for record in hits.get("records", []):
        where = (
            await task_ref(record["task_id"])
            if record.get("task_id")
            else room_ref(record["room_id"])
        )
        kind = _RECORD_NAMES.get(record["kind"], "文档段落")
        name, handle = record.get("author_name"), record["author"]
        who = f"{name}（{handle}）" if name and name != handle else handle
        lines.append(
            f"- {kind}，{who}，在频道「{record['room_title']}」（{where}）："
            f"{record['snippet']}"
        )
    for found in hits.get("library", []):
        lines.append(f"- 资料库文件：{found['path']}")
    for artifact in hits.get("artifacts", []):
        lines.append(f"- 产物「{artifact['name']}」：{artifact.get('about') or ''}")
    return lines


@router.post(
    "/assistant/agent/search", include_in_schema=False, dependencies=[ROUTE_DECIDES]
)
async def agent_search(
    body: AgentSearchIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """cheese_find: what the person can read that matches, across their
    projects or in one, in 芝士's words with the ref of each hit."""
    q = _query(body.query)
    actor = await resolver.resolve()
    if not actor.authenticated:
        raise AuthenticationRequiredError(say("invalidToken"))
    if body.project:
        project = await address.project_by_ref(db, body.project)
        if project is None:
            raise NotFoundError.for_resource("project", body.project)
        projects = [project]
    else:
        projects = (
            await ProjectService(db).list_visible_to(
                handle=actor.handle, user_id=actor.user_id
            )
        )[:AGENT_PROJECTS]
    terms = bm25.words(q)
    await bm25.serial_scans(db)
    parts: list[str] = []
    for project in projects:
        readable, _, reader = await _readable_rooms(
            db, resolver.for_reading(), project.id, None
        )
        shelf = await shelf_for(db, project.id)
        hits = await search_everything(
            db, project.id, q, terms, readable, AGENT_HITS, shelf
        )
        lines = await _agent_hits(db, project, hits, readable)
        if lines:
            parts.append(
                f"项目「{project.name}」（{project.slug}）：\n" + "\n".join(lines)
            )
    if not parts:
        where = f"项目 {body.project}" if body.project else "你的项目"
        return ok(
            {
                "text": f"在{where}里没有搜到「{q}」。每个词都要命中：换个说法或少几个词再搜。"
            }
        )
    looked = (
        ""
        if body.project or len(projects) < AGENT_PROJECTS
        else f"\n（只搜了最近的 {AGENT_PROJECTS} 个项目；要搜别的，给 project。）"
    )
    return ok({"text": "\n\n".join(parts) + looked})
