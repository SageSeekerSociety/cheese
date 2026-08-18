"""Request/response schemas for the project agents API."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentInstanceCreate(BaseModel):
    # Defaults to the type name, and to `cheese` when there is no type either.
    handle: str | None = Field(default=None, max_length=64)
    type_name: str | None = Field(default=None, max_length=64)
    display_name: str = Field(default="", max_length=64)


class AgentInstanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | None
    project_id: uuid.UUID
    handle: str
    type_name: str | None
    display_name: str
    # Whether this is the project's default — what a new topic gets.
    is_default: bool = False
    # False for the implicit 芝士 a project has before anyone configured one:
    # it resolves and it owns a memory pool, but there is no row to edit.
    configured: bool = True
    created_at: datetime | None = None


class ProjectDefaultAgentIn(BaseModel):
    """Set the project's default agent, by instance or by type.

    ``type_name`` is the shape the settings page uses — "which persona does 芝士
    wear in this project" — and applies to the project's default agent rather
    than making a second one.
    """

    instance_id: uuid.UUID | None = None
    type_name: str | None = Field(default=None, max_length=64)


class TopicAgentIn(BaseModel):
    instance_id: uuid.UUID | None = None
