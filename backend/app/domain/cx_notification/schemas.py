"""Notification request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.cx_notification.models import NotifKind, NotifLevel


class NotificationCreate(BaseModel):
    level: NotifLevel
    kind: NotifKind
    title: str = Field(min_length=1, max_length=300)
    body: str = ""
    target_handle: str | None = Field(default=None, max_length=64)
    topic_id: uuid.UUID | None = None
    payload: dict = Field(default_factory=dict)


class FeedbackIn(BaseModel):
    feedback: str


class ResolveIn(BaseModel):
    """拍板 payload. Deliberately only the choice: who decided is the request's
    resolved actor, so the field is gone rather than accepted-and-ignored — an
    ignored field still reads as supported in the OpenAPI schema."""

    chosen: str


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    topic_id: uuid.UUID | None
    level: NotifLevel
    kind: NotifKind
    target_handle: str | None
    title: str
    body: str
    payload: dict
    read_at: datetime | None
    resolved_at: datetime | None = None
    feedback: str | None
    created_at: datetime
