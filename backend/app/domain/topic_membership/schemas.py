"""Topic membership request/response schemas (Pydantic v2)."""

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.domain.topic.models import TopicRole


class TopicMemberCreate(BaseModel):
    handle: str = Field(min_length=1, max_length=64)


class TopicMemberOut(BaseModel):
    """One seat in a channel. 综合's people have no row of their own (everyone
    in the project is in it), so the seat is what is said about them, not a
    row's id."""

    model_config = ConfigDict(from_attributes=True)

    topic_id: uuid.UUID
    member_handle: str
    role: TopicRole
