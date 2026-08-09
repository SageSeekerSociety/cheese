"""Project membership request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.project.models import ProjectRole


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
