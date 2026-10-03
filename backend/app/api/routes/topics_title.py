"""The room's title, and the undo for one the platform gave it.

Sixth slice of `app/api/routes/topics.py` (arch review C-backend.md section 3.3),
after `topics_attachments.py` (#2171), `topics_documents.py` +
`topics_preview.py` (#2175), `topics_side_routes.py` (#2190) and
`topics_compute.py` (#2197). topics.py is 2,987 lines against a 1,500-line cap
that only ratchets down.

What moves: `POST /topics/{topic_id}/title` (a person naming the room),
`_title_actor` (the one helper both routes share) and
`POST /topics/{topic_id}/title/undo` (the button on the line that announced an
automatic rename). These are the only two places a person writes a room's name;
`topic/naming.py` owns the rename itself and stays where it is (`nudge` is still
called from topics.py), so what moves is where the HTTP surface lives, not who
owns the name. The paths say `topic_id` and the handlers name a thread when the
id is a thread's; that is the same place the rest of the module resolves, not a
second one.

What stays behind, and why. `Topic` is the one model name this group touches,
and it is imported from topics.py rather than from `app.domain.topic.models` on
purpose -- the shape `topics_side_routes.py` and `topics_compute.py` use, for the
same reason: the C2 contract in `.importlinter` ratchets (route module, model
module) pairs, and a direct import would add a line to that ratchet. topics.py
still reads `Topic` in its own `_topic_out` helper, so its line stays matched and
this move adds no exemption to any boundary. Everything else here keeps its home
and is imported from where it is defined; nothing here imported a name topics.py
read only for these routes. topics.py imports nothing from this module, so there
is no cycle.

Ordering. This module sorts after `topics.py` and after every other `topics_*`
module (`_` > `.`, and `title` > `side`), so its two paths mount later in the
route table than they did inside topics.py. No route registered before them --
in topics.py or in the modules mounted between -- has a parameter where `title`
sits, so neither loses its first match; resolving every path in the table
confirms each still reaches the handler it did before, now under
`app.api.routes.topics_title`.

The new module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`, so the declaration below, with
the same prefix and tags, is all it takes.
"""

import uuid

from fastapi import APIRouter

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.api.routes.topics import DbSession, Topic
from app.core.errors import ForbiddenError, ValidationError
from app.core.sentences import say
from app.domain.agent.runtime import announce_stale
from app.domain.topic import naming
from app.domain.topic.schemas import TopicOut
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics", tags=["topics"])


@router.post("/{topic_id}/title")
async def set_title(
    topic_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """给这个地方起/改标题 — used by both `cheese_title` (a person asked 芝士
    for this name) and the frontend sidebar rename UI (dual-use, like doc/split).
    Either way a person chose it, so the platform's naming leaves it alone from
    now on (`topic/naming.py`).

    Names the THREAD when the id is a thread's. Resolving only rooms did not
    fail here, which is what made it dangerous: a 分身 naming the piece of work
    it had just been handed would have renamed the whole room around it.
    """
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(topic_id=place.room_id, project_id=place.project_id)
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    title = (body.get("title") or "").strip()
    if not title:
        raise ValidationError(say("titleRequired"))
    await naming.rename_by_person(
        db,
        place.room,
        title[:80],
        by=actor.handle,
        reason="rename",
    )
    await db.flush()
    out = TopicOut.model_validate(place.room).model_dump(mode="json")
    await db.commit()
    await announce_stale(place.room_id, "topics")
    return ok(out)


async def _title_actor(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> tuple[Topic, str]:
    """The room and the signed-in person acting on its title."""
    room = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=room.id, project_id=room.project_id)
    await resolver.authorize_topic(actor, project_id=room.project_id, topic_id=room.id)
    if not actor.authenticated:
        raise ForbiddenError(say("renameSignIn"))
    return room, actor.handle


@router.post("/{topic_id}/title/undo")
async def undo_title(
    topic_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """撤销一次自动改名 (the button on the line that announced it). The old
    title comes back and, being a person's choice now, stays."""
    room, handle = await _title_actor(topic_id, db, resolver)
    try:
        event_id = uuid.UUID(str(body.get("event_id")))
    except ValueError as exc:
        raise ValidationError(say("eventIdInvalid")) from exc
    await naming.undo(db, room, event_id, by=handle)
    await db.flush()
    out = TopicOut.model_validate(room).model_dump(mode="json")
    await db.commit()
    await announce_stale(room.id, "topics")
    return ok(out)
