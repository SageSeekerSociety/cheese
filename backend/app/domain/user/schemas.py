"""User request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    handle: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=200)
    bio: str = ""
    interests: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class UserLogin(BaseModel):
    """极简登录 (Phase 0, spec §7.2): no password — the handle IS the identity.
    Get-or-create: a known handle signs in, a new one registers on the spot."""

    handle: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(default="", max_length=120)


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=200)
    bio: str | None = None
    interests: list[str] | None = None
    skills: list[str] | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    handle: str
    name: str
    email: str | None
    bio: str
    interests: list[str]
    skills: list[str]
    created_at: datetime
    updated_at: datetime
