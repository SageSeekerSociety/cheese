"""Topic request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.topic.models import TopicKind, TopicStatus


class TopicCreate(BaseModel):
    project_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)
    parent_id: uuid.UUID | None = None
    created_by: str | None = None


class TopicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    parent_id: uuid.UUID | None
    title: str
    kind: TopicKind
    status: TopicStatus
    created_at: datetime
    updated_at: datetime
    # Lifecycle markers (spec §6.3): who accepted, when archived, and — for an
    # upgraded topic — which block it grew from (for the 活引用 back-link).
    accepted_by: str | None = None
    accepted_at: datetime | None = None
    archived_at: datetime | None = None
    upgraded_from_block_id: uuid.UUID | None = None


class UpgradeBlockIn(BaseModel):
    created_by: str | None = None


class SplitIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    created_by: str | None = None


class ConclusionIn(BaseModel):
    conclusion: str = Field(min_length=1)


class DocEditIn(BaseModel):
    content: str
    author: str = "anonymous"
