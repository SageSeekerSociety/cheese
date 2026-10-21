"""The shape one saved teammate configuration has — nothing about what exists.

Which models a project can be pointed at is not a property of a saved teammate
(it is a property of the deployment and the project), and it is answered in
`app.domain.agent.model_catalog`; this module names a model only. Keeping the
two apart is what lets a teammate's saved configuration be read without reading
the gateway, the harnesses or the supply policy.
"""

from typing import Literal

from pydantic import BaseModel, Field

#: The thinking efforts a teammate can ask for, lowest first; the same words
#: as the gateway's ``cheese_efforts`` (``gateway.EFFORTS``).
Effort = Literal["low", "medium", "high", "max"]


class AgentConfiguration(BaseModel):
    """A saved role and optional model; None inherits the project main model.

    MCP servers are not here: they belong to the agent's type, and a session
    reads them from there (`agent_instance.services.type_of_seat`).
    """

    body: str = ""
    skills: list[str] = Field(default_factory=list)
    model: str | None = None
    execution_device_id: str | None = Field(default=None, max_length=64)
    # How hard the model thinks (`gateway.EFFORTS`). None leaves it to the
    # model. Kept when the model changes: a model that does not honour it runs
    # at its own default (`launch_env`), and the choice is back when one does.
    effort: Effort | None = None
    # At what share of the context window the conversation is compacted.
    # None leaves it to the harness, which waits until the window is nearly
    # full; a lower share compacts sooner. Claude Code only lowers its
    # threshold with it, never raises it.
    compact_percent: int | None = Field(default=None, ge=50, le=90)
