"""Space request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.space.models import SpaceKind


class SpaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: SpaceKind = SpaceKind.other
    description: str = ""


class SpaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: SpaceKind
    description: str
    created_at: datetime
