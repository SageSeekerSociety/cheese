"""支线: the replies under one message in a channel, held apart from the channel's
main line.

A 支线 is a conversation of its own (``conversations``, kind ``thread``): the
replies are blocks whose ``conversation_id`` is the 支线's id, and 芝士 answers
in it with a session of its own. The message it hangs under stays where it
was, in the channel's main line (``root_block_id``).

``reply_count`` and ``last_reply_at`` are kept here because every page of the
main line shows them under each message that has a 支线, and the overview
lists 支线 by the latter. The database keeps them: a message inserted into a
支线 counts itself (trigger ``blocks_thread_replied``, migration
``4562fd5e0eeb``), so no path that writes a reply can forget to.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Thread(Base):
    __tablename__ = "threads"
    __table_args__ = (
        UniqueConstraint("root_block_id", name="uq_threads_root_block_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # The channel whose main line the message is in.
    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    # The message the 支线 hangs under. One 支线 a message.
    root_block_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("blocks.id", ondelete="CASCADE")
    )
    reply_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_reply_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    #: 这条支线被收起来了（`thread.services.recycle`）：它报的错拖过了时限而没人回来
    #: 修，于是不再点亮它那间频道，也从默认的支线列表里挪开。块一条不删 —— 翻得到、
    #: 搜得到，在里面说一句或者点「恢复」就放回来。NULL = 开着。
    recycled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def recycled(self) -> bool:
        return self.recycled_at is not None

    @staticmethod
    async def of_root(session, block_id: uuid.UUID) -> "Thread | None":
        """The 支线 under a message, if it has one."""
        return await session.scalar(
            select(Thread).where(Thread.root_block_id == block_id)
        )
