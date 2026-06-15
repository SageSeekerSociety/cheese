"""Block response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.domain.block.models import AuthorType, BlockKind


class BlockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    topic_id: uuid.UUID
    kind: BlockKind
    author_type: AuthorType
    author: str
    content: str
    reply_to: uuid.UUID | None
    created_at: datetime
