"""What a memory consolidation (dream) turn spent, landed as the platform's.

Consolidating a project's memory is work the platform does on its own, so no
team pays for it (#2233). The turn runs on the project's platform gateway key
(`gateway_usage._model_kwargs(platform=True)`), never the key the project pays
for, so its spend cannot surface in a room turn's drain and be charged to the
team. Right after the turn, that key's new spend is drained into platform rows
under the dream kind, and the turn's own row is written beside them.

Both carry the project and the dream kind: the consolidation trigger sums the
project's usage minus that kind (`memory.dream`), so what consolidating costs
is never counted as "this project wrote a lot".
"""

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.domain.agent.gateway import LlmGateway
from app.domain.agent.gateway_spend import Charge, settle
from app.domain.agent.gateway_usage import TURN_RETRY_AFTER, ProjectGatewayKey
from app.domain.agent.service import AgentUsage
from app.domain.memory import dream
from app.domain.usage.ledger import Ledger


async def drain_dream_spend(
    sessions: async_sessionmaker,
    gateway: LlmGateway | None,
    gateway_lock: asyncio.Lock,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
) -> None:
    """Land what the project's platform key spent as the platform's."""
    await settle(
        sessions,
        gateway,
        ProjectGatewayKey(project_id, platform=True),
        Charge(
            model=settings.agent_model,
            kind=dream.DREAM_KIND,
            project_id=project_id,
            topic_id=topic_id,
            turn_id=turn_id,
        ),
        retry_after=TURN_RETRY_AFTER,
        lock=gateway_lock,
    )


async def record_dream_usage(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    root_topic_id: uuid.UUID,
    usage: AgentUsage | None,
    turn_id: uuid.UUID,
) -> None:
    """The dream turn's own row: that a consolidation ran here, and what the
    runtime reported for it. The hooks runtimes report no token counts, so
    its tokens are often 0; the row still says the turn happened."""
    model = usage.model if usage is not None and usage.model else settings.agent_model
    await Ledger(session).record_platform(
        kind=dream.DREAM_KIND,
        model=model,
        input_tokens=usage.input_tokens if usage is not None else 0,
        output_tokens=usage.output_tokens if usage is not None else 0,
        cost_usd=usage.cost_usd if usage is not None else 0.0,
        project_id=project_id,
        topic_id=root_topic_id,
        turn_id=turn_id,
        metered=usage is not None,
        route="",
    )
