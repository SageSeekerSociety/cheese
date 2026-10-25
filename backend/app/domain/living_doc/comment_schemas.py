"""Comment writes carry no caller-supplied authorship or anchor authority."""

import uuid
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from app.core.sentences import say


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


class CommentIn(BaseModel):
    """A new thread: what it says, and the words it is about (none: the whole
    document). A blank comment is refused by the route, with its reason."""

    content: str = Field(default="", max_length=16000)
    quote: str | None = None


class ThreadCommentOut(BaseModel):
    """One comment as the client reads it. ``anchor`` is where a thread on a
    room file points; a document's thread carries none, its own text does."""

    id: str
    author: str
    content: str
    anchor_quote: str | None
    anchor: dict | None = None
    created_at: str
    #: The people its @s told, added when a thread is read.
    notified: list[str] = Field(default_factory=list)


class ThreadOutsideOut(BaseModel):
    """The people a comment @s who cannot open the subject, said to its writer
    alone and never stored (the same shape a message's is)."""

    room_id: str
    handles: list[str]
    may_add: bool
    members_only: bool


class CommentCreatedOut(ThreadCommentOut):
    """A thread's opening comment as its writer gets it back: the comment, whom
    its @s told, and — to the writer alone — whom it named who cannot open the
    subject."""

    mentioned_outside: ThreadOutsideOut | None = None


class ThreadReplyOut(BaseModel):
    sequence: int
    comment: ThreadCommentOut


class ThreadOut(BaseModel):
    """One thread: its opening comment, its replies, and the revision a writer
    must have read before changing it."""

    comment: ThreadCommentOut
    revision: int
    state: str
    replies: list[ThreadReplyOut] = Field(default_factory=list)
    #: Whom it named who could not open the subject — the writer's answer only.
    mentioned_outside: ThreadOutsideOut | None = None


class ThreadListOut(BaseModel):
    data: list[ThreadOut]
    total: int


class ThreadAgentStoppedOut(BaseModel):
    """Nothing: the agent was told to stop, and its reply keeps what was
    written, marked as stopped."""
