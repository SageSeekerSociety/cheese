"""The living doc's node tree, and the comments written on it.

Tenth slice of `app/api/routes/topics.py` (arch review C-backend.md section
3.3), after `topics_attachments.py` (#2171), `topics_documents.py` +
`topics_preview.py` (#2175), `topics_side_routes.py` (#2190),
`topics_compute.py` (#2197), `topics_title.py` (#2201), `topics_shown.py`
(#2207), `topics_tasks.py` (#2215) and `topics_transcript.py` (#2218).
topics.py is 2,297 lines against a 1,500-line cap that only ratchets down; this
slice takes it to 2,188.

What moves, verbatim: `GET /topics/{topic_id}/docs` (the living doc's
structured node tree, B1 spec §5, in document order) and
`GET`/`POST /topics/{topic_id}/comments` (段落评论, eval B4: inline comments,
each anchored to a doc node via `reply_to`, plus the write side that lands one
without starting a room-agent turn). They are one group because they are two
halves of one surface -- the tree the comment panel draws and the comments that
hang off it: the read side of the doc panel's annotations.

What stays behind, and why. `/doc` and `/overview` stay in topics.py. Moving
`edit_topic_doc` would orphan `agent_notice`, its only reader there, and
`app.domain.block.models` is still a frozen C2 edge of `app.api.routes.topics`
(read for `AuthorType`, `Block`, `BlockKind`, `CHECKLIST_META_KEY`), so carrying
it here would ADD a line to `.importlinter` rather than rename one -- the
baseline may not grow. The names these three routes do read are the shared ones:
`_actor_in_place` (identity, then the room's roster) and `DbSession` are read by
a score of handlers that stay, so they keep their home and this module imports
them in. `BlockRepository` is imported from topics.py rather than from
`app.domain.block.repositories` for the second ratchet: the guard in
`tests/unit/test_domain_import_guard.py` counts (route module, repository
module) pairs, and topics.py still reads `BlockRepository` in a dozen handlers,
so importing it from the package that already owns that edge adds no exemption
to any boundary. `AuthorType` and `BlockKind` come from topics.py for the C2
reason above. Nothing here imports an `app.domain.*.models` module directly and
no repository reaches topics.py's set of route modules for the first time, so
neither baseline moves and no contract grows.

Ordering. Discovery walks `app.api.routes` in filename order, so this module
sorts after `topics.py` and `topics_attachments.py` and before the rest of the
`topics_*` family (`.` < `_`; `attachments` < `comments` < `compute`). Its three
routes therefore mount later in the route table than they did inside topics.py.
No route registered before them -- in topics.py or in the module mounted between
-- has a parameter where `docs` or `comments` sits, so none of the three loses
its first full match; resolving every path in the table confirms each still
reaches the handler it did before, now under
`app.api.routes.topics_comments`.

The new module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`, so the declaration below, with
the same prefix and tags, is all it takes.
"""

import uuid

from fastapi import APIRouter

from app.api.auth import ActorResolverDep
from app.api.response import ok, page
from app.api.routes.topics import (
    AuthorType,
    BlockKind,
    BlockRepository,
    DbSession,
    _actor_in_place,
)
from app.core.errors import ValidationError
from app.domain.block.notice_text import say
from app.domain.block.schemas import BlockOut
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics", tags=["topics"])


@router.get("/{topic_id}/docs")
async def list_topic_docs(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Document-tree view of a topic: the living doc's structured node tree
    (B1, spec §5) in document order."""
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    nodes = await BlockRepository(db).list_doc_nodes(place.room_id)
    items = [BlockOut.model_validate(b).model_dump(mode="json") for b in nodes]
    return ok(page(items, len(items)))


@router.get("/{topic_id}/comments")
async def list_comments(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """段落评论 (eval B4): inline comments, each anchored to a doc node via
    reply_to."""
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    comments = await BlockRepository(db).list_comments_for_topic(place.room_id)
    items = [BlockOut.model_validate(c).model_dump(mode="json") for c in comments]
    return ok(page(items, len(items)))


@router.post("/{topic_id}/comments")
async def add_comment(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Add an inline comment anchored to a doc node (eval B4). Dual-use like the
    doc panel — a human selects text and comments; not cheese-gated."""
    place = await TopicService(db).place_or_404(topic_id)
    anchor = (body.get("anchor") or "").strip()
    content = (body.get("content") or "").strip()
    if not content:
        raise ValidationError("评论内容不能为空")
    repo = BlockRepository(db)
    reply_to: uuid.UUID | None = None
    if anchor:
        try:
            anchor_id = uuid.UUID(anchor)
        except ValueError as exc:
            raise ValidationError(say("commentAnchorNotInDoc")) from exc
        node = await repo.get(anchor_id)
        root = await repo.doc_root(place.room_id)
        if (
            node is None
            or root is None
            or node.topic_id != place.room_id
            or node.task_id is not None
            or node.kind != BlockKind.doc_node
            or node.struct_parent != root.id
        ):
            raise ValidationError(say("commentAnchorNotInDoc"))
        reply_to = node.id
    # B4 Feishu-style: the exact selected span, kept for display next to the
    # comment. Bounded so a runaway selection can't bloat the row.
    quote = (body.get("quote") or "").strip() or None
    if quote and len(quote) > 500:
        quote = quote[:500]
    actor = await resolver.resolve(
        fallback_handle=body.get("author"),
        topic_id=place.room_id,
        project_id=place.project_id,
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    author = actor.handle
    comment = await repo.add(
        project_id=place.project_id,
        # The place id, not the room's: `add` splits it, and handing it the room
        # would post a thread's comment onto the room for everyone to read.
        topic_id=topic_id,
        author=author,
        author_type=AuthorType.participant,
        content=content,
        kind=BlockKind.comment,
        reply_to=reply_to,
        anchor_quote=quote,
    )
    payload = BlockOut.model_validate(comment).model_dump(mode="json")
    await db.commit()
    return ok(payload)
