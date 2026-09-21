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
    # 零读者、零写者：一个类型说的是角色，不说用哪个模型、哪个骨架、想多深
    # （结论 3、28）。``exclude=True`` 让它们不再进响应；字段本身在 P15b 随
    # 迁移一起删。
    model: str | None = Field(default=None, exclude=True)
    effort: str | None = Field(default=None, exclude=True)
    harness: str | None = Field(default=None, exclude=True)
    # Whether it ships with the platform — presets are read-only.
    builtin: bool
    space_id: int | None = None
    created_by: str | None = None
    created_at: datetime | None = None
