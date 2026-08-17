"""Alert request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.alert.models import AlertKind, AlertLevel


class AlertCreate(BaseModel):
    level: AlertLevel
    kind: AlertKind
    title: str = Field(min_length=1, max_length=300)
    body: str = ""
    target_handle: str | None = Field(default=None, max_length=64)
    topic_id: uuid.UUID | None = None
    payload: dict = Field(default_factory=dict)


class FeedbackIn(BaseModel):
    feedback: str


class ResolveIn(BaseModel):
    # NB: no ``decided_by`` — the decider is the verified caller (ActorResolver),
    # never a body field. A client still sending it is silently ignored.
    chosen: str


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    topic_id: uuid.UUID | None
    level: AlertLevel
    kind: AlertKind
    target_handle: str | None
    title: str
    body: str
    payload: dict
    read_at: datetime | None
    resolved_at: datetime | None = None
    feedback: str | None
    created_at: datetime
