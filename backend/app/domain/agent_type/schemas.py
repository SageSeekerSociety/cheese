"""Request/response schemas for the agent-types API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=128)
    description: str = ""
    # The system prompt this agent runs under (= the markdown body of a preset).
    #
    # Optional, and empty is a real answer rather than a half-filled form: an
    # agent is made "that agent" by the memory it accumulates, which is injected
    # every turn regardless. Requiring prose up front asks people to invent a
    # personality before the agent has done anything — and the agent can write
    # its own later.
    body: str = ""
    skills: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    model: str | None = Field(default=None, max_length=64)
    effort: str | None = Field(default=None, max_length=16)
    harness: str | None = Field(default=None, max_length=32)
    # NULL space_id = a personal type; set = owned by that institution.
    space_id: int | None = None
    created_by: str = Field(default="", max_length=128)


class AgentTypeUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=128)
    description: str | None = None
    body: str | None = None  # "" clears it — see AgentTypeCreate.body
    skills: list[str] | None = None
    mcp_servers: list[str] | None = None
    model: str | None = Field(default=None, max_length=64)
    effort: str | None = Field(default=None, max_length=16)
    harness: str | None = Field(default=None, max_length=32)


class AgentTypeOut(BaseModel):
    """One entry of the merged catalog (preset file or custom DB row)."""

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
