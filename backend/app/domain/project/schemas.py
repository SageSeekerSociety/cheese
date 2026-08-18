"""Project request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.project.models import AiMode


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    owner_handle: str | None = None
    ai_mode: AiMode = AiMode.collaborative
    # The agent type the project's default 芝士 wears. Stored on that agent, not
    # on the project: the persona is one of the type's properties, and the
    # project only says which agent is the default.
    agent_type: str | None = None
    # 项目归团队 (v4): the shared team this project belongs to. Omitted → the
    # owner's personal team is resolved server-side.
    team_id: int | None = None
    # The 赛题 this project comes from, when it was created from one.
    external_task_id: int | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    owner_handle: str | None
    team_id: int | None = None
    external_task_id: int | None = None
    ai_mode: AiMode
    summary: str
    root_topic_id: uuid.UUID | None
    created_at: datetime


class TaskLinkCreate(BaseModel):
    task_id: uuid.UUID


class TaskLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    task_id: uuid.UUID
    created_at: datetime
