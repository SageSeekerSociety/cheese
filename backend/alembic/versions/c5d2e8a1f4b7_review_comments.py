"""review comments: comments on a task's changes, sent with a 退回

Revision ID: c5d2e8a1f4b7
Revises: d3f6a2c85b71

One row per comment a person writes on lines of a task's changes while it
awaits review: a draft until the next 退回 sends it, then what 芝士 reports
having done about it. A new table; nothing existing is altered, so the release
still serving never reads it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "c5d2e8a1f4b7"
down_revision: str | Sequence[str] | None = "d3f6a2c85b71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The two foreign keys lock the tables they point at.
    with_lock_retries("tasks, accept_cards")
    op.create_table(
        "review_comments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey(
                "tasks.id", ondelete="CASCADE", name="fk_review_comments_task_id_tasks"
            ),
            nullable=False,
        ),
        sa.Column("author_handle", sa.String(64), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("line_start", sa.Integer(), nullable=False),
        sa.Column("line_end", sa.Integer(), nullable=False),
        sa.Column("line_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("commit_sha", sa.String(64), nullable=True),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("suggestion", sa.Text(), nullable=True),
        sa.Column(
            "parent_id",
            sa.Uuid(),
            sa.ForeignKey(
                "review_comments.id",
                ondelete="CASCADE",
                name="fk_review_comments_parent_id_review_comments",
            ),
            nullable=True,
        ),
        sa.Column("state", sa.String(16), nullable=False, server_default="draft"),
        sa.Column(
            "card_id",
            sa.Uuid(),
            sa.ForeignKey(
                "accept_cards.id",
                ondelete="SET NULL",
                name="fk_review_comments_card_id_accept_cards",
            ),
            nullable=True,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("outcome_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('draft', 'sent')", name="ck_review_comments_state"
        ),
        sa.CheckConstraint(
            "outcome IN ('handled', 'not_handled')", name="ck_review_comments_outcome"
        ),
    )
    op.create_index(
        "ix_review_comments_task_id_created_at",
        "review_comments",
        ["task_id", "created_at"],
    )
    op.create_index("ix_review_comments_parent_id", "review_comments", ["parent_id"])
    op.create_index("ix_review_comments_card_id", "review_comments", ["card_id"])


def downgrade() -> None:
    op.drop_table("review_comments")
