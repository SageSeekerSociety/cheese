"""A comment a person writes on lines of a task's changes while it awaits review.

One row is one comment: on a range of lines of one file in the version under
review, or a reply under another comment. It is written as a draft that only
its author sees, survives a reload, and goes to 芝士 with the next 退回; from then
on it is sent, belongs to the card it went back with, and carries what 芝士 did
about it when it hands the task over again.

Its own table, not a kind of document comment or chat block: a document comment
is anchored inside a shared document, a block is something said in a
conversation. This is anchored to a path and lines of a version of a branch, and
has a state the review flow moves.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class ReviewCommentState(enum.StrEnum):
    #: Written, not yet sent: only its author sees it.
    draft = "draft"
    #: Went to 芝士 with a 退回.
    sent = "sent"


class ReviewCommentOutcome(enum.StrEnum):
    """What 芝士 said it did about a sent comment when it handed the task over."""

    handled = "handled"
    not_handled = "not_handled"


class ReviewComment(UuidPk, Timestamps, Base):
    __tablename__ = "review_comments"
    __table_args__ = (
        # A task's comments, read every time its 改动 tab opens.
        Index("ix_review_comments_task_id_created_at", "task_id", "created_at"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tasks.id", ondelete="CASCADE", name="fk_review_comments_task_id_tasks"
        )
    )
    author_handle: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(1024))
    #: The lines, numbered as in the version under review (the new side of the
    #: diff). A reply carries its parent's.
    line_start: Mapped[int] = mapped_column(Integer)
    line_end: Mapped[int] = mapped_column(Integer)
    #: Those lines' text when the comment was written: line numbers move between
    #: rounds, and this is what finds the comment's place in the next version.
    line_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: Where the comment points, in the file's own terms: `L12-L14` for lines,
    #: and for files without lines `p3` (a page), `s2` (a slide) or `汇总!C5`
    #: (a cell). See `comment_place`.
    place: Mapped[str] = mapped_column(String(255), default="", server_default="")
    #: The commit the lines were read from.
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    body: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: A 修改建议: what the lines should read instead.
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "review_comments.id",
            ondelete="CASCADE",
            name="fk_review_comments_parent_id_review_comments",
        ),
        nullable=True,
        index=True,
    )
    state: Mapped[ReviewCommentState] = mapped_column(
        Enum(
            ReviewCommentState,
            native_enum=False,
            length=16,
            create_constraint=True,
            name="ck_review_comments_state",
        ),
        default=ReviewCommentState.draft,
        server_default=ReviewCommentState.draft.value,
    )
    #: The card it was sent back with.
    card_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "accept_cards.id",
            ondelete="SET NULL",
            name="fk_review_comments_card_id_accept_cards",
        ),
        nullable=True,
        index=True,
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    outcome: Mapped[ReviewCommentOutcome | None] = mapped_column(
        Enum(
            ReviewCommentOutcome,
            native_enum=False,
            length=16,
            create_constraint=True,
            name="ck_review_comments_outcome",
        ),
        nullable=True,
    )
    #: 芝士's one line on how it was handled, or why not.
    outcome_note: Mapped[str | None] = mapped_column(Text, nullable=True)
