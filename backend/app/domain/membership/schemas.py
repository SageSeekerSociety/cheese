"""Project membership request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime
from typing import Literal

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
    #: The role they take once they accept.
    role: Literal["member", "mentor"] = "member"


class InvitationRespond(BaseModel):
    accept: bool


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    invitee_handle: str
    inviter_handle: str
    #: The inviter's own 芝士 sent it for them.
    invited_by_assistant: bool = False
    status: InvitationStatus
    created_at: datetime
    responded_at: datetime | None
    role: str = "member"


class RoleSet(BaseModel):
    role: Literal["admin", "member", "mentor"]


class GrantsIn(BaseModel):
    """What each role named may do; a role left out keeps what it has."""

    grants: dict[Literal["admin", "member", "mentor"], list[str]]


class TransferCreate(BaseModel):
    to_handle: str = Field(min_length=1, max_length=64)


class TransferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    from_handle: str
    to_handle: str
    status: InvitationStatus
    created_at: datetime
    responded_at: datetime | None


class MoveIn(BaseModel):
    team_id: int


class RoleOut(BaseModel):
    """Where the person sits now."""

    role: str | None


class RoleGrantsOut(BaseModel):
    """What each role may do in the project, and whether any differs from its
    defaults."""

    grants: dict[str, list[str]]
    changed: bool


class MovedOut(BaseModel):
    project_id: uuid.UUID
    team_id: int
