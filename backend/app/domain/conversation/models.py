"""Conversations: the register of every place a conversation happens.

A room and a task are each a conversation, and the things that belong to one —
what is said in it, an agent's session, its turns and spend, its progress —
point here (``conversation_id``) rather than at whichever table the
conversation lives in. A row holds only who the conversation is: its id
(the room's or the task's own), its project and its kind. Everything a room or
a task says about itself stays in ``topics`` or ``tasks``.

The database keeps the register, not the application: inserting a room or a
task registers it and deleting one removes it (migration ``f7985445d2bf``), so
no code path creates or deletes these rows.
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ConversationKind(enum.StrEnum):
    room = "room"
    task = "task"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[ConversationKind] = mapped_column(
        Enum(ConversationKind, native_enum=False, length=16)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
