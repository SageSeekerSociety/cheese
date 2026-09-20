"""Request/response schemas for the agent-types API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentTypeOut(BaseModel):
    """One built-in starting configuration."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    title: str
    description: str
    body: str
    skills: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    model: str | None = None
    effort: str | None = None
    harness: str | None = None
    # Whether it ships with the platform — presets are read-only.
    builtin: bool
    space_id: int | None = None
    created_by: str | None = None
    created_at: datetime | None = None
