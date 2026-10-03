"""Typed data accepted by the room's single message door."""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.sentences import say


class SlidePageQuoteIn(BaseModel):
    """A page of text, or a passage selected inside one."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["slide-page"]
    path: str = Field(min_length=1)
    source: Literal["live", "committed"]
    version: str = Field(min_length=1)
    task_id: uuid.UUID | None = None
    page: int = Field(gt=0, strict=True)
    # 读者指的是整页还是这一页里的一段；决定了 `text` 是整页文字还是选中的那段。
    # 默认成整页是为了读得懂这之前发出来的消息 —— 那时候还没有这个字段，而能有的
    # 只有整页。新发的一律显式带上。
    scope: Literal["page", "selection"] = "page"
    text: str


class PagePinQuoteIn(BaseModel):
    """A point on a page: `x`/`y` are ratios of the page's width and height, not pixels.

    Ratios because the page is redrawn to the panel width and readers zoom: a pixel
    coordinate only holds for the revision it was taken on, a ratio points back to the
    same spot on any of them.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["page-pin"]
    path: str = Field(min_length=1)
    source: Literal["live", "committed"]
    version: str = Field(min_length=1)
    task_id: uuid.UUID | None = None
    page: int = Field(gt=0, strict=True)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


QuotedContextIn = Annotated[
    SlidePageQuoteIn | PagePinQuoteIn, Field(discriminator="kind")
]


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
                raise ValueError(say("quoteNeedsMessage"))
            strings = self.quoted_context.model_dump(mode="json").values()
            if (
                len(self.content) + sum(len(v) for v in strings if isinstance(v, str))
                > 100000
            ):
                raise ValueError(say("quoteTooLong", limit=100000))
        return self
