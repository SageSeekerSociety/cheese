"""What makes a handle an AI teammate, as a SQL predicate other reads can
carry alongside their own rows."""

from sqlalchemy import ColumnElement, exists
from sqlalchemy.orm import QueryableAttribute

from app.domain.identity.models import AgentBinding
from app.domain.user.models import User


def names_a_live_agent(
    handle: ColumnElement[str] | ColumnElement[str | None] | QueryableAttribute,
) -> ColumnElement[bool]:
    """True where ``handle`` names a live account carrying an agent binding:
    the answer `IdentityService.agents_among` and
    `AgentBindingRepository.agent_handles` give, and what the
    ``("is_agent", handle)`` request fact holds."""
    return exists().where(
        User.username == handle,
        User.deleted_at.is_(None),
        AgentBinding.user_id == User.id,
    )
