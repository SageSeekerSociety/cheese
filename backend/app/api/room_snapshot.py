"""What a room looks like at the moment a subscription to it takes effect.

A page opening a room used to ask for it a piece at a time — the roster, the
tasks, the pins, the threads, the proposal cards, and on a task page the task,
what it came from and its review comments — a dozen requests before the room
was drawn. The subscription the page opens anyway (`routes/chat.py`) now
carries them in its `subscribed` frame as `room`.

It is read after the subscription is registered and sent before any of the
room's frames, so every frame that follows is newer than it: a change made
while the snapshot was being read arrives as a frame behind it, never lost
between a read and a subscription. A resubscription after a dropped link gets
a fresh one.

Each piece is exactly what its own route returns (`GET /topics/{id}/members`,
`…/tasks`, `…/pins`, `…/threads`, `…/feedback-proposals`, the room's skill
proposals; for a task `GET /topics/{task}/task`, `…/related`,
`…/review-comments`), built by the same function the route calls, so a client
files each one where that route's answer goes and reads it from there after.
Two are left out because reading them can wait on another service: the accept
card refreshes PR state from the forge, and the preview probes the running
app. Neither may hold a room's subscription up, so both stay requests of their
own. The room's messages are not here either: the page asks for the newest
page while its code is still loading, before any subscription exists.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.project_skills import proposals_in_room
from app.api.routes.review_comments import review_comment_rows
from app.api.routes.topic_members import roster_rows
from app.api.routes.topics_pins import pin_rows
from app.api.routes.topics_tasks import related_of, room_task_items, task_page_row
from app.core.errors import NotFoundError
from app.domain.agent.chat import ChatService
from app.domain.feedback.proposals import ProposalService
from app.domain.identity.actor import Actor
from app.domain.thread import reads as thread_reads
from app.domain.topic.services import TopicService

#: How many recently finished tasks a channel's overview shows beside the open
#: ones (the client's `RECENT_DONE`).
RECENT_DONE = 3
#: How many threads a channel's overview and its main line read at once.
THREADS = 100


async def room_snapshot(
    db: AsyncSession, chat: ChatService, actor: Actor, conversation_id: uuid.UUID
) -> dict | None:
    """The `room` of a `subscribed` frame for this conversation (a channel or a
    task), or None when it is neither — a thread's own line, say. The caller
    has authorised `actor` for the room."""
    try:
        place = await TopicService(db).place_or_404(conversation_id)
    except NotFoundError:
        return None
    room = place.room
    viewer = actor.handle if actor.authenticated else None
    snapshot: dict = {
        "members": await roster_rows(db, room),
        "feedback_proposals": await ProposalService(db).live_cards(conversation_id),
        "skill_proposals": await proposals_in_room(db, place.project_id, place.room_id),
    }
    task = place.task
    if task is None:
        snapshot["tasks"] = {
            "open": await room_task_items(
                db, chat, actor, room, limit=0, status="open"
            ),
            "recent": await room_task_items(
                db, chat, actor, room, limit=0, status="closed", latest=RECENT_DONE
            ),
        }
        snapshot["pins"] = await pin_rows(db, place.room_id)
        snapshot["threads"] = await thread_reads.in_room(
            db, place.room_id, viewer=viewer, limit=THREADS
        )
    else:
        snapshot["task"] = await task_page_row(db, chat, place, task)
        snapshot["related"] = await related_of(db, task)
        snapshot["review_comments"] = await review_comment_rows(
            db, task, actor.handle or ""
        )
    return snapshot
