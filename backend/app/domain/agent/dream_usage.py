"""What a memory consolidation (dream) spent, charged to its project.

Consolidating a project's memory is work inside the project, so the project's
team pays for it like any other turn there, and its usage says it was the
dream. Each of its passes is one call the backend makes on the project's dream
key (`memory_ledger._dream_scope`); right after them, the dream key's new spend
is drained as the dream's (`DREAM_KIND`). No room turn reads that key, so none
can take the dream's spend as its own. The consolidation trigger leaves that
kind out of "this project wrote a lot" (`memory/dream.py`).
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import model_roles
from app.domain.agent.gateway import LlmGateway
from app.domain.agent.gateway_spend import Charge, settle
from app.domain.agent.gateway_usage import (
    LATE_S,
    TURN_RETRY_AFTER,
    ProjectGatewayKey,
)
from app.domain.agent.service import AgentUsage
from app.domain.memory import dream
from app.domain.usage.ledger import Ledger, payer_for_project


async def drain_dream_spend(
    sessions: async_sessionmaker,
    gateway: LlmGateway | None,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
) -> list[AgentUsage] | None:
    """Charge what the project's dream key spent to the project, as the
    dream's. Read once more after `LATE_S` if the gateway has not logged it
    yet: nobody waits on a dream, and what is still unread is charged by the
    next dream's drain."""
    return await settle(
        sessions,
        gateway,
        ProjectGatewayKey(project_id, dream=True),
        Charge(
            model=model_roles.model("agent_model"),
            kind=dream.DREAM_KIND,
            topic_id=topic_id,
            turn_id=turn_id,
        ),
        retry_after=(*TURN_RETRY_AFTER, LATE_S),
    )


async def record_dream_usage(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    root_topic_id: uuid.UUID,
    turn_id: uuid.UUID,
    drained: bool,
) -> None:
    """When nothing was drained for the dream, a row charging nothing that
    says a consolidation ran here (``<kind>:unmetered``), as a room turn with
    no measured usage leaves one (`turn_usage.record_turn_usage`). A dream
    whose spend was drained is already on the ledger, so nothing more is
    written."""
    if drained:
        return
    await Ledger(session).record(
        await payer_for_project(session, project_id),
        credits=0.0,
        kind=dream.DREAM_KIND,
        model=model_roles.model("agent_model"),
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        topic_id=root_topic_id,
        turn_id=turn_id,
        metered=False,
        route="gateway",
    )
