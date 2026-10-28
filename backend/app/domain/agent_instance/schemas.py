"""Request/response schemas for the project agents API."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.agent_instance.configuration import AgentConfiguration


class AgentInstanceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configuration: AgentConfiguration | None = None
    # A missing handle is assigned once at creation and remains stable.
    handle: str | None = Field(default=None, max_length=64)
    type_name: str | None = Field(default=None, max_length=64)
    display_name: str = Field(default="", max_length=64)


class AgentInstanceUpdate(BaseModel):
    """Edit the name or saved configuration of one agent.

    ``expected_version`` is the version this edit was built from — what the
    screen read before the person started changing it. Given, the save lands
    only while the agent is still at it, and a 409 says somebody wrote first.
    Omitted, the save replaces the configuration unconditionally.
    """

    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, max_length=64)
    configuration: AgentConfiguration | None = None
    expected_version: int | None = None


class AgentInstanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    handle: str
    # The handle this agent sits on room rosters as. Inviting it into a room is
    # adding this to the roster, the same call that adds a person.
    seat_handle: str
    type_name: str | None
    display_name: str
    # ``default``: the name it was born with, which a screen shows in its
    # reader's language.
    name_source: str = "human"
    configuration: AgentConfiguration
    # Sent back on every read so a save can carry it: a save built from a stale
    # one is refused rather than overwriting whoever wrote first.
    version: int = 1
    # Whether this is the project's default — what a new topic gets.
    is_default: bool = False
    # False = retired. Still listed, still resolvable by the rooms already on
    # it, still owns its memory — just not on offer for new work.
    is_active: bool = True
    # The handle editing this agent right now, or None. A courtesy read, not a
    # gate: a stale save is refused by ``version`` whatever this says.
    editing: str | None = None
    created_at: datetime | None = None


class ProjectDefaultAgentIn(BaseModel):
    """Select the saved agent that new rooms start with."""

    instance_id: uuid.UUID
