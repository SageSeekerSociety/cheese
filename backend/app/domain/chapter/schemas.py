"""What a bookmark looks like to the screen that lists it."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.domain.block.schemas import BlockOut


class ChapterOut(BaseModel):
    """One bookmark: the message it marks, and who marked it when."""

    pinned_by: str
    pinned_at: datetime
    block: BlockOut


class ChapterMarkOut(BaseModel):
    """What marking or unmarking a message answers with."""

    block_id: uuid.UUID
    chapter: bool
