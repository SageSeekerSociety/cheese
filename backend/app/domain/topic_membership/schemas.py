"""Topic membership request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.topic.models import TopicRole


class TopicMemberCreate(BaseModel):
    handle: str = Field(min_length=1, max_length=64)
    role: TopicRole = TopicRole.member


class TopicMemberRoleUpdate(BaseModel):
    role: TopicRole


class TopicMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    topic_id: uuid.UUID
    member_handle: str
    role: TopicRole
    created_at: datetime
