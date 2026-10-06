"""What a channel keeps at the top of its overview: messages from its main line
and files sent in it.

A file sent in a channel is a block of its own (kind ``attachment``) next to the
message it came with, so a pin names one block either way. It goes when the
block goes.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk


class Pin(UuidPk, Base):
    __tablename__ = "pins"

    #: The channel whose main line the block is in.
    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    block_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("blocks.id", ondelete="CASCADE"), unique=True
    )
    pinned_by: Mapped[str] = mapped_column(String(128))
    pinned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
