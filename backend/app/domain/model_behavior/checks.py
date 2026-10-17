"""Open and settle model-behavior checks (`models.ModelBehaviorCheck`)."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.model_behavior.models import ModelBehaviorCheck


class Kind(StrEnum):
    #: A task's teammate was asked to write the task up and close it with
    #: `cheese_close_task`, was reminded once, and did not.
    TASK_CLOSE_NOT_CALLED = "task_close_not_called"


class Outcome(StrEnum):
    DONE = "done"
    MISSED = "missed"
    MOOT = "moot"


async def asked(
    session: AsyncSession,
    *,
    kind: Kind,
    ask_id: uuid.UUID,
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    agent_handle: str,
    model: str | None,
    at: datetime,
) -> None:
    """The ask reached the teammate: one row per ask, however often this is
    said."""
    await session.execute(
        insert(ModelBehaviorCheck)
        .values(
            id=uuid.uuid4(),
            kind=kind.value,
            ask_id=ask_id,
            project_id=project_id,
            conversation_id=conversation_id,
            agent_handle=agent_handle,
            model=model,
            asked_at=at,
        )
        .on_conflict_do_nothing(constraint="uq_model_behavior_checks_kind_ask")
    )


async def settle(
    session: AsyncSession,
    *,
    kind: Kind,
    ask_id: uuid.UUID,
    outcome: Outcome,
    at: datetime,
) -> None:
    """How the ask went; the first answer stands."""
    await session.execute(
        update(ModelBehaviorCheck)
        .where(
            ModelBehaviorCheck.kind == kind.value,
            ModelBehaviorCheck.ask_id == ask_id,
            ModelBehaviorCheck.outcome.is_(None),
        )
        .values(outcome=outcome.value, settled_at=at)
    )
