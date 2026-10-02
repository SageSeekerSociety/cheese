"""Typed data accepted by the room's single message door."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class QuotedContextIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["slide-page"]
    path: str = Field(min_length=1)
    source: Literal["live", "committed"]
    version: str = Field(min_length=1)
    task_id: uuid.UUID | None = None
    page: int = Field(gt=0, strict=True)
    text: str


class ChatAttachmentIn(BaseModel):
    """A file uploaded beforehand, by path."""

    path: str = Field(min_length=1)
    mime: str = ""


class ChatMessageIn(BaseModel):
    content: str = Field(default="", max_length=100000)
    request_id: uuid.UUID
    reply_to: uuid.UUID | None = None
    attachments: list[ChatAttachmentIn] = Field(default_factory=list)
    quoted_context: QuotedContextIn | None = None

    @model_validator(mode="after")
    def quote_is_bounded_message_data(self):
        if self.quoted_context is not None:
            if not self.content.strip():
                raise ValueError("quoted context requires authored message content")
            strings = self.quoted_context.model_dump(mode="json").values()
            if (
                len(self.content) + sum(len(v) for v in strings if isinstance(v, str))
                > 100000
            ):
                raise ValueError("message and quoted context exceed 100000 characters")
        return self
