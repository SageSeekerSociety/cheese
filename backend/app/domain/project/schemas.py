"""Project request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.project.models import AiMode


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    owner_handle: str | None = None
    ai_mode: AiMode = AiMode.collaborative
    expert_role: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    owner_handle: str | None
    team_id: int | None = None
    ai_mode: AiMode
    expert_role: str | None
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
