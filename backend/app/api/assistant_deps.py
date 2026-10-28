"""How a person's 芝士 is let into a project's things: as the person opening
their pages would be.

Both the question (`routes/assistant.py`, the page it is asked from) and the
reads it makes (`routes/assistant_agent.py`) judge a project's page with this
one class, and route modules do not import each other.
"""

from __future__ import annotations

import uuid

from fastapi.requests import HTTPConnection

from app.api.auth import ActorResolver
from app.api.doc_access import is_team_member, reach
from app.core import credentials
from app.core.credentials import Kind
from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.agent.personal import service as personal
from app.domain.assistant import proposals
from app.domain.room_task.place import place_or_404
from app.domain.team.services import team_service
from app.domain.topic.services import TopicService


async def not_by_their_assistant(connection: HTTPConnection) -> None:
    """The panel is the person's own hold on their 芝士 — asking it, stopping
    it, what it asks first, what it remembers, undoing what it did — so 芝士
    acting for them is not let in: what is weighed does not reset what weighs
    it. 芝士 itself works through ``/assistant/agent``."""
    presented = credentials.presented(connection.headers)
    if credentials.kind_of(presented) is Kind.DELEGATED:
        raise ForbiddenError(say("assistantPanelIsThePersons"))


class ProjectPages:
    """Whether the person may open a project's page, judged by the request's
    own resolver as opening that page is (``pages.PageAccess``,
    ``reading.Access``) — as a read: asking and reading only look."""

    def __init__(self, db, resolver: ActorResolver) -> None:
        self._db = db
        self._resolver = resolver.for_reading()

    async def topic(self, topic_id: uuid.UUID):
        place = await place_or_404(self._db, topic_id)
        actor = await self._resolver.resolve(
            topic_id=place.conversation_id, project_id=place.project_id
        )
        await self._resolver.authorize_topic(
            actor, project_id=place.project_id, room=place.room
        )
        return place

    async def document(self, document_id: uuid.UUID) -> None:
        await reach(self._db, self._resolver, document_id)

    async def project(self, project_id: uuid.UUID) -> None:
        actor = await self._resolver.resolve(project_id=project_id)
        await self._resolver.authorize_project(actor, project_id=project_id)

    async def team(self, team_id: int):
        """A team the person is a member of, as its library and its own
        documents are opened."""
        actor = await self._resolver.resolve()
        team = await team_service(self._db).get_team(team_id)
        if team is None or not await is_team_member(self._db, team_id, actor.handle):
            raise NotFoundError.for_resource("team", team_id)
        return team

    async def channels(self, project_id: uuid.UUID) -> list:
        actor = await self._resolver.resolve(project_id=project_id)
        await self._resolver.authorize_project(actor, project_id=project_id)
        topics, _, _ = await TopicService(self._db).list_for_project(
            project_id, viewer=actor.handle
        )
        readable = await self._resolver.readable_topic_ids(
            actor, project_id=project_id, topics=topics
        )
        return [t for t in topics if t.id in readable]


async def show_proposal(db, questions, claims, kind: str, payload: dict) -> dict:
    """Keep what 芝士 put to the person to confirm, and show it on the
    question's stream as a card (``proposal``); the card as the panel takes
    it. Committed: the card outlives the request."""
    row = await proposals.propose(
        db, user_id=claims.user_id, work=claims.work, kind=kind, payload=payload
    )
    await db.commit()
    shown = proposals.view(row)
    try:
        asked = await questions.get(claims.work)
    except RuntimeError:  # no Valkey: nobody to tell
        asked = None
    if asked is not None and asked.kind == personal.KIND:
        await questions.publish(asked, "proposal", shown)
    return shown


def where_of(place) -> str:
    """How a conversation is named on a card: a channel by its name, a task or
    a 支线 by its title."""
    if place.task is not None:
        return place.task.title
    if place.thread is not None:
        return place.thread.title
    return place.room.title


async def tell_change(questions, claims, shown: dict) -> None:
    """Show a change 芝士 made on the question's stream (``change``), where the
    panel lists it with 撤销."""
    try:
        asked = await questions.get(claims.work)
    except RuntimeError:  # no Valkey: nobody to tell
        return
    if asked is not None and asked.kind == personal.KIND:
        await questions.publish(asked, "change", shown)


async def tell_handed(questions, claims, handed: dict) -> None:
    """Show a file 芝士 handed the person on the question's stream
    (``handed``): the answer carries it as a card the person downloads."""
    try:
        asked = await questions.get(claims.work)
    except RuntimeError:  # no Valkey: nobody to tell
        return
    if asked is not None and asked.kind == personal.KIND:
        await questions.publish(asked, "handed", handed)


async def tell_rule(questions, claims, rule: dict) -> None:
    """Show a rule 芝士 made or changed on the question's stream (``rule``):
    the answer carries it as a card, with what it waits for and a way to call
    it off."""
    try:
        asked = await questions.get(claims.work)
    except RuntimeError:  # no Valkey: nobody to tell
        return
    if asked is not None and asked.kind == personal.KIND:
        await questions.publish(asked, "rule", rule)
