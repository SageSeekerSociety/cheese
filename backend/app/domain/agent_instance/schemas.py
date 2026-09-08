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
    """Edit the name or saved configuration of one agent."""

    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, max_length=64)
    configuration: AgentConfiguration | None = None


class AgentInstanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | None
    project_id: uuid.UUID
    handle: str
    type_name: str | None
    display_name: str
    configuration: AgentConfiguration
    # Whether this is the project's default — what a new topic gets.
    is_default: bool = False
    # Retained for older clients; every project roster entry now has a saved row.
    configured: bool = True
    # False = retired. Still listed, still resolvable by the rooms already on
    # it, still owns its memory — just not on offer for new work.
    is_active: bool = True
    created_at: datetime | None = None


class ProjectDefaultAgentIn(BaseModel):
    """Select the saved agent that new rooms start with."""

    instance_id: uuid.UUID


class TopicAgentIn(BaseModel):
    instance_id: uuid.UUID | None = None
