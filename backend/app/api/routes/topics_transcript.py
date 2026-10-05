"""What a room did: its 施工现场 (spec §7.1) and the tail of one step's output.

Ninth slice of `app/api/routes/topics.py` (arch review C-backend.md section
3.3), after `topics_attachments.py` (#2171), `topics_documents.py` +
`topics_preview.py` (#2175), `topics_side_routes.py` (#2190),
`topics_compute.py` (#2197), `topics_title.py` (#2201), `topics_shown.py`
(#2207) and `topics_tasks.py` (#2215). topics.py is 2,403 lines against a
1,500-line cap that only ratchets down.

What moves, verbatim: `GET /topics/{topic_id}/transcript` (the room's session
record -- its tool/event actions, read-only, newest window first, pageable and
filterable by author) and `GET /topics/{topic_id}/transcript/{block_id}/output`
(the kept tail of what one 现场 step printed). They are one group because the
second is the reason the first leaves the output behind: a page of 120 steps
would otherwise carry up to 120 × 8 KiB, so a transcript row only says it has
some (`output_bytes`) and this endpoint hands it over when somebody opens it.

What stays behind, and why. `_actor_in_place` (identity, then the room's roster
-- the shape every `topics_*` module imports it in), `DbSession`, `Block`,
`BlockKind`, `BlockOut` and `BlockRepository` are imported from topics.py rather
than from `app.domain.block.*`. `BlockRepository` for the guard in
`tests/unit/test_domain_import_guard.py`, which ratchets (route module,
repository module) pairs: coming from the module that already owns that edge
adds no exemption. `Block` and `BlockKind` for the C2 baseline in
`.importlinter`, which ratchets `app.domain.block.models`: importing it here
would add an edge. `without_output` is the one name topics.py read only for the
transcript, so it leaves with the code and is imported from
`app.domain.agent.step_output` directly. topics.py imports nothing from this
module, so there is no cycle.

Ordering. This module sorts after `topics.py` and after every other `topics_*`
module (`_` > `.`, and `transcript` > `title`), so its two paths mount later in
the route table than they did inside topics.py. Both are literal where
`transcript` sits, and no route registered in between has a parameter there, so
neither loses its first full match; resolving every path in the table confirms
each still reaches the handler it did before, now under
`app.api.routes.topics_transcript`. OpenAPI is byte-identical apart from the
moved paths' position in the paths object.

The new module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`, so the declaration below, with
the same prefix and tags, is all it takes.
"""

import uuid

from fastapi import APIRouter, Query

from app.api.auth import ActorResolverDep
from app.api.response import ok, page
from app.api.routes.topics import (
    Block,
    BlockKind,
    BlockOut,
    BlockRepository,
    DbSession,
    _actor_in_place,
)
from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.agent.step_output import without_output
from app.domain.agent.turn_times import turn_starts
from app.domain.room_task.services import TaskService
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics", tags=["topics"])


@router.get("/{topic_id}/transcript")
async def topic_transcript(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    limit: int | None = Query(None, ge=1, le=200),
    before: uuid.UUID | None = None,
    author: str | None = Query(None, min_length=1, max_length=120),
    task: uuid.UUID | None = None,
) -> dict:
    """施工现场 (spec §7.1): the topic's AI session record — tool/event actions,
    read-only, newest window first.

    Paged for the same reason the conversation is: events are the MOST numerous
    kind of block (one per tool call), so a topic that has run for a while makes
    this the largest response the app can ask for, and it only ever grows.
    `limit=None` keeps the whole-history behaviour for callers that still want
    it.

    `author` narrows it to one teammate's steps (一个人/一个队友的 handle). A room
    can seat several of them, and 现场 can be read one of them at a time; that
    filter belongs INSIDE the paging, exactly like `kinds` — filtering a page
    after the fact returns fewer rows than asked for and reports `has_more`
    against the wrong set, so the caller pages through holes.

    The room's own line, or with `task` that task's: each conversation's
    session has its own record, and interleaving every task's actions into the
    room's would bury what the room did."""
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    if task is not None:
        resolver.require_task_scope(task)
        await TaskService(db).require_in_room(place.room_id, task)
    repo = BlockRepository(db)
    # 现场 = what 芝士 DID (tool/system events), full stop. Its messages belong
    # to the conversation pane — mirroring them here just duplicates the chat.
    kinds = {BlockKind.event}
    cursor: Block | None = None
    if before is not None:
        cursor = await repo.get(before)
        # Same rule as the conversation's pager: an unknown cursor must not
        # degrade into "newest N", which the caller cannot tell from a real page.
        # A cursor from another conversation in this room is as wrong as one
        # from another room.
        if cursor is None or cursor.topic_id != place.room_id or cursor.task_id != task:
            raise NotFoundError(say("cursorEventNotFound"))
    if limit is None:
        site = [
            b
            for b in await repo.list_for_topic(place.room_id, task_id=task)
            if b.kind in kinds and (author is None or b.author == author)
        ]
        has_more = False
    else:
        result = await repo.page_for_topic(
            place.room_id,
            task_id=task,
            limit=limit,
            before=cursor,
            kinds=kinds,
            author=author,
        )
        site, has_more = result.items, result.has_more
    # What a step printed stays behind: a page of 120 steps would otherwise
    # carry up to 120 × 8 KiB. The row says it has some (`output_bytes`), and
    # `step_output` below hands it over when somebody opens it.
    items = [
        without_output(BlockOut.model_validate(b).model_dump(mode="json")) for b in site
    ]
    # A turn's first step comes after its preparation and the model's first
    # answer; the 现场 counts the turn from when it started.
    starts = await turn_starts(db, task or place.room_id, (b.turn_id for b in site))
    return ok(
        {
            **page(items, len(items)),
            "has_more": has_more,
            "oldest_id": str(site[0].id) if site else None,
            "turn_starts": {
                str(turn_id): started.isoformat() for turn_id, started in starts.items()
            },
        }
    )


@router.get("/{topic_id}/transcript/{block_id}/output")
async def step_output(
    topic_id: uuid.UUID,
    block_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    task: uuid.UUID | None = None,
) -> dict:
    """The tail of what one 现场 step printed, as kept (``step_output``)."""
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    if task is not None:
        resolver.require_task_scope(task)
        await TaskService(db).require_in_room(place.room_id, task)
    block = await BlockRepository(db).get(block_id)
    # Same door as the transcript: the one conversation it was asked about.
    if (
        block is None
        or block.topic_id != place.room_id
        or block.task_id != task
        or block.kind != BlockKind.event
    ):
        raise NotFoundError(say("stepNotFound"))
    meta = block.meta or {}
    return ok(
        {
            "output": str(meta.get("output") or ""),
            "bytes": int(meta.get("output_bytes") or 0),
        }
    )
