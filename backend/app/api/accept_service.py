"""The one place the API layer builds the acceptance service.

`review` sits in layer 4, `delivery` and `agent_instance` in layer 5: the
functions it cannot reach are handed in from here (`app.domain.review.ports`).
A door that hands in one fewer says one line fewer, or cannot tell which AI
teammate it is — so every route that decides a card builds the service here:
the accept-card routes, and the channel's main line adopting a delivery on its
reviewer's word (`topics_tasks.accept_coordinated_task`).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent import execution
from app.domain.agent.announce import announce
from app.domain.agent_instance.adapters import project_team, task_teammate
from app.domain.delivery.agent import record_seat_instruction, record_task_instruction
from app.domain.delivery.ledger import settle_card
from app.domain.machine import hand_in
from app.domain.review.services import AcceptService


def accept_service(db: AsyncSession) -> AcceptService:
    """The acceptance service, with everything it cannot reach handed in."""
    return AcceptService(
        db,
        announce=announce,
        settle_card=settle_card,
        record_instruction=record_task_instruction,
        record_seat=record_seat_instruction,
        task_teammate=task_teammate,
        roster_team=project_team,
        executor=execution.call,
        uncommitted=hand_in.uncommitted,
    )
