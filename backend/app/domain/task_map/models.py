"""The `task_map_cards` table: one task's card, one row per task."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class TaskMapCard(Base):
    """The card a task shows at the top of its page and, later, on the map.

    A task with no row has no card yet: the page says so rather than showing
    empty fields. Each field may be empty on its own; an empty field is not
    shown.
    """

    __tablename__ = "task_map_cards"

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tasks.id", ondelete="CASCADE", name="fk_task_map_cards_task_id_tasks"
        ),
        primary_key=True,
    )
    # 是什么: what the task is for, in a sentence.
    what: Mapped[str] = mapped_column(Text, default="", server_default="")
    # 到哪了: where it has got to, in a sentence.
    progress: Mapped[str] = mapped_column(Text, default="", server_default="")
    # 等谁: [{"handle": <project member>, "ask": <what is wanted of them>}].
    waiting_on: Mapped[list[dict]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # 名词: [{"term": ..., "meaning": ...}].
    terms: Mapped[list[dict]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # Who wrote the card last: a person's handle, or the teammate's.
    updated_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
