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
    space_id: int
    name: str
    description: str
    resource_pack: dict
    conditions: list[dict]
    default_role: str | None
    published: bool
    created_at: datetime


# ---- 匹配市场 (spec §13 阶段 6) ----


class MarketTaskOut(BaseModel):
    """A published template as it appears in the market catalog."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    space_id: int
    space_name: str
    name: str
    description: str
    resource_pack: dict
    conditions: list[dict]
    default_role: str | None
    created_at: datetime


class ApplicationCreate(BaseModel):
    project_id: uuid.UUID
    pitch: str = Field(default="", max_length=4000)


class ApplicationDecide(BaseModel):
    # Who acts for the Space (no fine-grained Space permissions yet, MVP).
    decided_by: str | None = Field(default=None, max_length=64)


class TaskApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    template_id: uuid.UUID
    project_id: uuid.UUID
    project_name: str = ""
    pitch: str
    status: str
    decided_by: str | None
    decided_at: datetime | None
    task_id: uuid.UUID | None
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
