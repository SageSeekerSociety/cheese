"""Topic membership request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.topic.models import TopicRole


class TopicMemberCreate(BaseModel):
    handle: str = Field(min_length=1, max_length=64)
    role: TopicRole = TopicRole.member
    # No auth layer yet (fusion-design §4 agent-as-user is P1): the acting user's
    # handle is passed explicitly and the service authorizes against their role.
    actor: str = Field(min_length=1, max_length=64)


class TopicMemberRoleUpdate(BaseModel):
    role: TopicRole
    actor: str = Field(min_length=1, max_length=64)


class TopicMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    topic_id: uuid.UUID
    member_handle: str
    role: TopicRole
    created_at: datetime
