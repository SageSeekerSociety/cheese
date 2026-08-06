"""Milestone request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.milestone.models import MilestoneStatus


class MilestoneCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    due_date: datetime | None = None
    source_topic_id: uuid.UUID | None = None
    auto_pinned: bool = False


class MilestoneUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    due_date: datetime | None = None
    status: MilestoneStatus | None = None


class MilestoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    description: str
    due_date: datetime | None
    status: MilestoneStatus
    source_topic_id: uuid.UUID | None
    auto_pinned: bool
    created_at: datetime
