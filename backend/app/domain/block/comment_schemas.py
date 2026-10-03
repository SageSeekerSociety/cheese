"""Thread mutations have no caller-supplied authorship or anchor authority."""

import uuid
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from app.domain.block.notice_text import say


def parse_uuid(value):
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        raise ValueError("UUID must be a string")
    return uuid.UUID(value)


class ThreadMutation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation_id: Annotated[uuid.UUID, BeforeValidator(parse_uuid)]
    expected_revision: int = Field(ge=1)


class ReplyIn(ThreadMutation):
    content: str = Field(min_length=1, max_length=16000)

    @field_validator("content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError(say("commentContentRequired"))
        return value
