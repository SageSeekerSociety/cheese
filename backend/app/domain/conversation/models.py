"""Conversations: the register of every place a conversation happens.

A room, a task and a 支线 are each a conversation, and so is a person's own
芝士 conversation, which is in no project; and the things that belong to one —
what is said in it, an agent's session, its turns and spend, its progress —
point here (``conversation_id``) rather than at whichever table the
conversation lives in. A row holds who the conversation is: its id
(the room's, the task's or the 支线's own), its project and its kind. Everything
each says about itself stays in ``topics``, ``tasks`` or ``threads``.

The database keeps the register, not the application: inserting a room, a
task, a 支线 or a person's 芝士 conversation registers it and deleting one removes it (migration ``f7985445d2bf``), so
no code path creates or deletes these rows.
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ConversationKind(enum.StrEnum):
    room = "room"
    task = "task"
    thread = "thread"
    #: A person's own 芝士 conversation (``assistant_conversations``), in no
    #: project.
    personal = "personal"


class Conversation(Base):
    __tablename__ = "conversations"

    raw_records_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    # None for a personal conversation, and only for one.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    kind: Mapped[ConversationKind] = mapped_column(
        Enum(ConversationKind, native_enum=False, length=16)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
