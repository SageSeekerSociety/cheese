"""What a bookmark looks like to the screen that lists it."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.domain.block.schemas import BlockOut


class ChapterOut(BaseModel):
    """One bookmark: the message it marks, who marked it when, and what it is
    called (None until the message has words to name it with)."""

    pinned_by: str
    pinned_at: datetime
    title: str | None = None
    block: BlockOut


class ChapterMarkOut(BaseModel):
    """What marking or unmarking a message answers with."""

    block_id: uuid.UUID
    chapter: bool


class ChapterTitleOut(BaseModel):
    """What renaming a bookmark answers with."""

    block_id: uuid.UUID
    title: str
