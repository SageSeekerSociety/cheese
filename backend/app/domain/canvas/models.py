"""A canvas, its cards and the lines between them.

A canvas is a block of one document: the document's Markdown holds a line that
names it (``app.domain.canvas.block``), and the board itself is kept here, in
three tables, because a board is three kinds of thing. It belongs to its
document for good — whoever may read the document may read it, whoever may
write the document may change it — and goes with it. Copying the document, or
the block, makes a new canvas; two documents never share one.

Nothing about what a card or a line means is fixed. A card is a passage of
Markdown, written by whoever put it there; a line may carry a few words of its
own. A card may point at a task, which the board then shows with its state.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk


def _now() -> datetime:
    return datetime.now(UTC)


class Canvas(UuidPk, Base):
    __tablename__ = "canvases"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="CASCADE",
            name="fk_canvases_document_id_documents",
        ),
        index=True,
    )
    #: How tall the board is drawn inside the document, in pixels; None is the
    #: default height. Somebody dragged its bottom edge.
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The number the next card gets. Cards are named by number (``[3]``) to
    #: whoever reads the board as text, so a number is never reused.
    next_number: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class CanvasCard(UuidPk, Base):
    """One card: a passage of Markdown at a place on the board."""

    __tablename__ = "canvas_cards"
    __table_args__ = (
        UniqueConstraint("canvas_id", "number", name="uq_canvas_cards_canvas_number"),
        Index("ix_canvas_cards_task_id", "task_id"),
    )

    canvas_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "canvases.id", ondelete="CASCADE", name="fk_canvas_cards_canvas_id_canvases"
        )
    )
    #: Its number on this canvas, from 1.
    number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: Where it sits, its top-left corner. None until somebody places it: the
    #: board lays such a card out by its lines.
    x: Mapped[float | None] = mapped_column(Float, nullable=True)
    y: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Its size; None is the board's default.
    width: Mapped[float | None] = mapped_column(Float, nullable=True)
    height: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: The task this card stands for, if any: the board shows its state and
    #: opens it.
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "tasks.id", ondelete="SET NULL", name="fk_canvas_cards_task_id_tasks"
        ),
        nullable=True,
    )
    #: Who last wrote, moved or resized it, and when — what tells an AI
    #: teammate a person has just been at it.
    updated_by: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CanvasEdge(UuidPk, Base):
    """A line from one card to another, with a few words on it or none."""

    __tablename__ = "canvas_edges"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "target_id", name="uq_canvas_edges_source_target"
        ),
        Index("ix_canvas_edges_target_id", "target_id"),
        Index("ix_canvas_edges_canvas_id", "canvas_id"),
    )

    canvas_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "canvases.id", ondelete="CASCADE", name="fk_canvas_edges_canvas_id_canvases"
        )
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "canvas_cards.id",
            ondelete="CASCADE",
            name="fk_canvas_edges_source_id_canvas_cards",
        )
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "canvas_cards.id",
            ondelete="CASCADE",
            name="fk_canvas_edges_target_id_canvas_cards",
        )
    )
    label: Mapped[str] = mapped_column(String(200), default="", server_default="")
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
