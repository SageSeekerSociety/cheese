"""Project membership request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.project.models import InvitationStatus


class MemberCreate(BaseModel):
    user_handle: str = Field(min_length=1, max_length=64)


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    user_handle: str
    created_at: datetime


class InvitationCreate(BaseModel):
    user_handle: str = Field(min_length=1, max_length=64)


class InvitationRespond(BaseModel):
    accept: bool


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    invitee_handle: str
    inviter_handle: str
    status: InvitationStatus
    created_at: datetime
    responded_at: datetime | None
