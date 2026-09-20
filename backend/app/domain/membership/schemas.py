"""Project membership request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.project.models import InvitationStatus, ProjectRole


class MemberCreate(BaseModel):
    user_handle: str = Field(min_length=1, max_length=64)
    role: ProjectRole = ProjectRole.member


class MemberRoleUpdate(BaseModel):
    role: ProjectRole


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    user_handle: str
    role: ProjectRole
    created_at: datetime


class InvitationCreate(BaseModel):
    user_handle: str = Field(min_length=1, max_length=64)
    role: ProjectRole = ProjectRole.member


class InvitationRespond(BaseModel):
    accept: bool


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    invitee_handle: str
    inviter_handle: str
    role: ProjectRole
    status: InvitationStatus
    created_at: datetime
    responded_at: datetime | None
