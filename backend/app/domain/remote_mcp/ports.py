"""What ``remote_mcp`` asks about teammates that only a higher layer answers.

A project's remote servers are the ones its own ``.mcp.json`` declares plus the
ones its teammates' *types* declare, and a type is an ``AgentTypeDef`` — that
lives in ``app.domain.agent_type.library``, layer 2, which this package may
read. What it may not read is which types this project's active seats are, and
which of them a given ``agent_handle`` is: both are answered from the
agent-instance tables, and ``app.domain.agent_instance`` sits *above* this
package in the layer table (``docs/manual/dev/architecture.md``).

So the two questions are answered on the other side of that line and reach this
package as calls the caller hands in. ``app.domain.agent_instance.services``
already has both, under these shapes — ``project_types`` and ``type_of_seat``
— so nothing new is written on that side, and each caller passes them: the
settings page's routes (``app.api.routes.remote_mcp``), the notice a session
start writes about servers nobody connected yet
(``app.domain.agent.mcp_notice``), and the target a session is launched with
(``app.domain.agent.central_provider``). Same rule as the session factory in
``app.domain.webhook``: whoever calls gives.

No function here needs both: what is declared for a *room* is the project's
types, what is declared for a *seat* is that one seat's type.
"""

import uuid
from collections.abc import Sequence
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_type.library import AgentTypeDef


class ProjectTypes(Protocol):
    """The agent types this project's active seats are, each once."""

    async def __call__(
        self, db: AsyncSession, project_id: uuid.UUID
    ) -> Sequence[AgentTypeDef]:
        """The types of the project's active teammates, deduplicated; empty for
        a project with no active seat."""
        ...


class TypeOfSeat(Protocol):
    """The type of the teammate acting as one handle, if it has one."""

    async def __call__(
        self, db: AsyncSession, project_id: uuid.UUID, handle: str | None
    ) -> AgentTypeDef | None:
        """``None`` when the project is gone, no handle was given, or that
        seat's type cannot be resolved — the shared ``cheese`` seat included,
        when the project's default instance carries no type."""
        ...
