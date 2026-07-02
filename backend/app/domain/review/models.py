"""Accept card model — spec §4.4, §6.3, eval C5.

When a topic's main deliverable is ready, 芝士 hands the accept card to a
specific person ("等 XX 验收"), not a broadcast. Hard rule (spec §4.4): in
collaborative mode AI cannot accept its own work. Accept is recorded by name and
revocable; accepting = merge + archive the topic.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class AcceptStatus(enum.StrEnum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    revoked = "revoked"
    # 采纳时 merge 冲突：话题不归档、卡片进入此状态，芝士被派去解决冲突，
    # 解决后由人重试采纳（spec §6.3 冲突处理：芝士先尝试解决）。
    conflict = "conflict"


class AcceptCard(UuidPk, Timestamps, Base):
    __tablename__ = "accept_cards"

    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    # Routed reviewer (spec C5): the specific person asked to accept.
    reviewer_handle: Mapped[str] = mapped_column(String(64), index=True)
    # Why this reviewer was suggested (最懂/没参与/有空), for transparency.
    routing_reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[AcceptStatus] = mapped_column(
        Enum(AcceptStatus, native_enum=False, length=16),
        default=AcceptStatus.pending,
    )
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    note: Mapped[str] = mapped_column(Text, default="")
