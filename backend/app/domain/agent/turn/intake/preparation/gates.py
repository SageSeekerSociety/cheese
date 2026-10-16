"""The two things that stop a turn between its machine and its model.

`no_provider_bail` is the first: the deployment lists nothing that runs here,
and the room is told which harness was wanted rather than a missing default
model. It is asked before the tier policy on purpose — that step resolves a
model, and a harness that is not deployed points at none, so asking it first
ends in "no default model" and the room hears the wrong reason.

`check_model_policy` is the second, and its placement is the load-bearing part:
after the model is resolved and before it is occupied. Past that point a request
goes out, and a policy call arriving then makes "this turn did not happen"
untrue — which is the premise both a proposal and a refusal share. An execution
environment does not pass this gate: it belongs to its own teammate, is resolved
the first time it is needed (`machine/session_work`), and whether it spends the
project's money is said by its specification.

Both bodies moved here verbatim from `preparation`.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.core.sentences import say
from app.domain.agent.queries import (
    _bail_notice,
    _model_policy_call,
    _pass_policy_gate,
)
from app.domain.agent.turn.intake.context import TurnBail, _proposal_frames
from app.domain.agent_session.services import AgentSessionService
from app.domain.policy import gate
from app.domain.usage.ledger import team_terms
from app.domain.usage.model_access import overseas_approved

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.domain.agent.room.sessions import RoomSessions
    from app.domain.agent_instance.services import ResolvedAgent
    from app.domain.project.models import Project
    from app.domain.room_task.place import Place


async def no_provider_bail(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    turn_id: uuid.UUID,
    harness: str,
) -> TurnBail:
    """Return the frames that end a turn whose machine runs nothing this
    deployment lists, naming the harness that was wanted."""
    return TurnBail(
        [
            {
                "type": "event_block",
                "block": await _bail_notice(
                    project_id=project_id,
                    topic_id=room_id,
                    turn_id=turn_id,
                    session=session,
                    text=say("harnessNotDeployed", harness=harness),
                ),
            },
            {"type": "done"},
        ]
    )


async def check_model_policy(
    session: AsyncSession,
    *,
    provider: RoomSessions,
    project: Project | None,
    needs_place: bool,
    place: Place,
    agent: ResolvedAgent,
    harness: str,
    acting_agent: str,
) -> TurnBail | None:
    """Pass the tier policy for the model this turn is about to occupy; return
    the frames that stop it, or ``None`` once the turn may go on."""
    if provider.deferred_work and project is not None and needs_place:
        conversation = await AgentSessionService(session).ensure(
            place.conversation_id, agent.handle, harness=harness
        )
        if conversation.execution_request is None:
            conversation.execution_request = {"generation": str(uuid.uuid4())}
    if project is not None:
        actor_handle = acting_agent or agent.handle
        tiers = (await team_terms(session, project.team_id)).model_tiers
        policy = gate.policy_of(
            project.settings,
            tiers,
            overseas_approved=await overseas_approved(session, project.team_id),
        )
        proposed = await _pass_policy_gate(
            session,
            place.room_id,
            _model_policy_call(project, agent),
            policy,
            actor=actor_handle,
        )
        if proposed is not None:
            await session.commit()
            return TurnBail(_proposal_frames(proposed.landed))
    return None
