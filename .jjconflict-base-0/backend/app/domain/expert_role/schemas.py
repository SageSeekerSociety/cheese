"""Request/response schemas for the roles API (spec §8.2)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=128)
    description: str = ""
    # The persona system prompt (= the markdown body of a role file).
    body: str = Field(min_length=1)
    # NULL space_id = a personal role; set = owned by that institution.
    space_id: int | None = None
    created_by: str = Field(default="", max_length=128)


class RoleUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=128)
    description: str | None = None
    body: str | None = Field(default=None, min_length=1)


class RoleOut(BaseModel):
    """One entry of the merged catalog (built-in file or custom DB row)."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    title: str
    description: str
    body: str
    builtin: bool
    space_id: int | None = None
    created_by: str | None = None
    created_at: datetime | None = None
