"""Strict inputs for journal actions; actor identity never comes from the body."""

import uuid

from pydantic import BaseModel, ConfigDict, Field


class RestoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    expected_version: int = Field(ge=0)
    operation_id: uuid.UUID
