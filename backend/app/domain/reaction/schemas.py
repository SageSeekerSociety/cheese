"""Pydantic request/response schemas for 消息表情回应 (message reactions)."""

from pydantic import BaseModel, Field


class ReactionBody(BaseModel):
    """Emoji carried in the body (avoids emoji-in-URL encoding issues)."""

    emoji: str = Field(min_length=1, max_length=32)


class ReactionsQueryBody(BaseModel):
    block_ids: list[int] = Field(default_factory=list)


class EmojiReaction(BaseModel):
    """One reaction chip: an emoji, how many put it, who, and whether the actor did."""

    emoji: str
    count: int
    user_ids: list[int]
    me: bool
