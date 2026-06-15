"""Task Template & Task request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TaskTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    resource_pack: dict = Field(default_factory=dict)
    conditions: list[dict] = Field(default_factory=list)
    default_role: str | None = Field(default=None, max_length=64)


class TaskTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    space_id: uuid.UUID
    name: str
    description: str
    resource_pack: dict
    conditions: list[dict]
    default_role: str | None
    created_at: datetime


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    template_id: uuid.UUID
    title: str
    description: str
    created_at: datetime
