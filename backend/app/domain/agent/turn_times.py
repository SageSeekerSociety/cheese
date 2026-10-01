"""When a room's turns started, for the room's 现场 to say how long each took.

A turn's own steps do not say it: the first of them comes after the turn was
prepared and the model first answered, which can be most of the turn. The
moment the platform decided to speak is `AgentTurn.started_at`, the same for a
turn a person, a schedule or the agent itself started.
"""

import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.repositories import AgentTurnRepository


async def turn_starts(
    db: AsyncSession, topic_id: uuid.UUID, turn_ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, datetime]:
    return await AgentTurnRepository(db).started_at_of(
        topic_id, (turn_id for turn_id in turn_ids if turn_id is not None)
    )
