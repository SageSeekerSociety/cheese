"""A task's card on the project map (`app/domain/task_map`): read it, change it.

`GET|PATCH /topics/{task}/map-card`. The card follows the task's document: whoever
may enter the task's channel reads it, and the task's owner and collaborators
or the task's own session change it, while the task is open
(`api/doc_access.require_writable`). Its AI teammate writes it with
`cheese_map_card`; a person edits it on the task page.

Where the task came from is not here: the task page reads it with the rest of
the task's origin (`GET /topics/{task}/related`, `task_origin.source`) and shows
it beside the card.
"""

import uuid

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.response import ok, typed_response
from app.api.routes.topics import DbSession
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import ForbiddenError, NotFoundError, UnprocessableEntityError
from app.core.sentences import say
from app.domain.identity.actor import Actor
from app.domain.project.services import ProjectArchivedError, refuse_writes_if_archived
from app.domain.room_task.place import Place
from app.domain.room_task.services import TaskService
from app.domain.task_map import card
from app.domain.task_map.card import MapCardIn, MapCardView
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics", tags=["topics"])


async def _task_place(
    db: AsyncSession, resolver: ActorResolver, topic_id: uuid.UUID, *, writing: bool
) -> tuple[Place, Actor]:
    place = await TopicService(db).place_or_404(topic_id)
    if place.task is None or place.thread is not None:
        raise NotFoundError(say("taskNotFound"))
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=writing
    )
    return place, actor


async def _refuse_change(
    db: AsyncSession, resolver: ActorResolver, place: Place, actor: Actor
) -> None:
    """The task document's write rule (`doc_access.require_writable`), for the card."""
    task = place.task
    assert task is not None
    TopicService(db).require_doc_writable(place)
    try:
        TaskService.require_open(task)
    except UnprocessableEntityError:
        raise UnprocessableEntityError(say("taskCardClosed")) from None
    own_session = resolver.credential_conversation() == task.id
    if not own_session and not (
        actor.authenticated and TaskService.takes_part(task, actor.handle)
    ):
        raise ForbiddenError(say("taskCardParticipantsOnly"))
    await refuse_writes_if_archived(db, place.project_id)


async def _view(
    db: AsyncSession, resolver: ActorResolver, place: Place, actor: Actor
) -> dict:
    task = place.task
    assert task is not None
    try:
        await _refuse_change(db, resolver, place, actor)
        can_edit = True
    except (UnprocessableEntityError, ForbiddenError, ProjectArchivedError):
        can_edit = False
    return {
        "card": await card.read(db, task.id),
        "can_edit": can_edit,
    }


@router.get("/{topic_id}/map-card", **typed_response(MapCardView))
async def read_map_card(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The task's card, and whether the caller may change it."""
    place, actor = await _task_place(db, resolver, topic_id, writing=False)
    return ok(await _view(db, resolver, place, actor))


@router.patch(
    "/{topic_id}/map-card",
    dependencies=[ROUTE_DECIDES],
    **typed_response(MapCardView),
)
async def change_map_card(
    topic_id: uuid.UUID,
    body: MapCardIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Change the card: the fields given are replaced, the rest kept. Its own
    session writes it after a turn (`cheese_map_card`); a person edits it on the
    task page."""
    place, actor = await _task_place(db, resolver, topic_id, writing=True)
    await _refuse_change(db, resolver, place, actor)
    await card.write(
        db,
        task_id=place.conversation_id,
        project_id=place.project_id,
        change=body,
        by=actor.handle,
    )
    view = await _view(db, resolver, place, actor)
    await db.commit()
    return ok(view)
