"""Seeding and reading the open-turn intervals a sweep acts on.

The registry used to be a JSON file, so a test could invent a turn on a topic id
that existed nowhere. It is a table now, with the same foreign key to ``topics``
the rest of the schema has — which is a truer stage anyway: the sweep POSTS into
these topics, so a topic that does not exist was never a valid setup.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.domain.agent.models import AgentTurn
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService


async def a_topic(factory, *, title: str = "T") -> uuid.UUID:
    """A real project + topic to hang turns off."""
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title=title, created_by="u"
        )
        topic_id = topic.id
        await session.commit()
    return topic_id


async def open_turn(
    factory,
    topic_id: uuid.UUID,
    *,
    turn_id: uuid.UUID | None = None,
    continuation_id: uuid.UUID | None = None,
    age_s: float = 60.0,
    author: str = "u",
    content: str = "修一下登录页",
    is_resume: bool = False,
    resendable: bool = True,
    delivered: bool = False,
) -> uuid.UUID:
    """One turn interval left open — what a process that died mid-turn leaves.

    ``age_s`` backdates ``started_at``: how long the interval has been open is
    what every sweep branch reads (too young to judge, stale enough to give up).
    """
    turn_id = turn_id or uuid.uuid4()
    started_at = datetime.now(UTC) - timedelta(seconds=age_s)
    async with factory() as session:
        await AgentTurnRepository(session).open(
            turn_id=turn_id,
            topic_id=topic_id,
            continuation_id=continuation_id or turn_id,
            author=author,
            content=content,
            is_resume=is_resume,
            resendable=resendable,
            started_at=started_at,
        )
        if delivered:
            await AgentTurnRepository(session).mark_delivered(turn_id, started_at)
        await session.commit()
    return turn_id


async def open_turn_ids(factory) -> set[uuid.UUID]:
    """Which intervals are still open. Empty means every turn has been accounted
    for — the assertion that used to read "the registry file is empty"."""
    async with factory() as session:
        return {
            record.turn_id for record in await AgentTurnRepository(session).open_turns()
        }


async def turn_row(factory, turn_id: uuid.UUID) -> AgentTurn | None:
    """The whole row, closed or not — for asserting on how an interval ENDED."""
    async with factory() as session:
        return await session.get(AgentTurn, turn_id)
