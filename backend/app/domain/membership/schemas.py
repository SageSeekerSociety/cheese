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
    #: The role they take once they accept: a role's key, or ``mentor`` for
    #: the project's mentor role, made the first time it is asked for.
    role: str = Field(default="member", min_length=1, max_length=64)


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
    role_id: uuid.UUID | None = None


class RoleSet(BaseModel):
    #: A role's key — ``admin``, ``member``, one of the project's own — or
    #: ``mentor`` for its mentor role.
    role: str = Field(min_length=1, max_length=64)


class RoleCreate(BaseModel):
    """A new role: named, its permissions copied from another role or taken from
    a template. A templated role may go unnamed and show the template's name."""

    name: str | None = Field(default=None, max_length=64)
    copy_from: str | None = Field(default=None, max_length=64)
    template: Literal["mentor"] | None = None


class RoleUpdate(BaseModel):
    """What changes: the name of one of the project's own roles, what a role
    may do, or both. Left out stays as it is."""

    name: str | None = Field(default=None, max_length=64)
    permissions: list[str] | None = None


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
    """The key of the role the person holds now."""

    role: str | None


class ProjectRoleOut(BaseModel):
    """One role of the project."""

    key: str
    #: None for a built-in role and a templated one never renamed: the screen
    #: names those.
    name: str | None
    builtin: str | None
    template: str | None
    permissions: list[str]
    #: A built-in role's defaults, for putting them back; none for the others.
    defaults: list[str] | None
    holders: int


class ProjectRolesOut(BaseModel):
    roles: list[ProjectRoleOut]


class RoleDeletedOut(BaseModel):
    deleted: bool


class MovedOut(BaseModel):
    project_id: uuid.UUID
    team_id: int
