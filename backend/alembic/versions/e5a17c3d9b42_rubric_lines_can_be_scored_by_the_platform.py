"""rubric lines can be scored by the platform

Revision ID: e5a17c3d9b42
Revises: 85dc942e2596
Create Date: 2026-11-16

A 评分要点 line says who scores it (`task_rubric_item.kind`): the grader by
hand, as every existing line is, or the platform from the credits the claim
spent (`usage`), with that line's settings in `config`. A review on such a task
keeps how its total was made (`task_submission_review.auto_scores`): the
grader's points for the manual lines and the automatic lines' scores. Every
column has a default or is nullable, so the running backend keeps writing
rows unchanged while this applies.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e5a17c3d9b42"
down_revision: str | Sequence[str] | None = "85dc942e2596"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("task_rubric_item,task_submission_review")
    op.add_column(
        "task_rubric_item",
        sa.Column("kind", sa.String(16), nullable=False, server_default="manual"),
    )
    op.add_column(
        "task_rubric_item",
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "task_submission_review",
        sa.Column(
            "auto_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )


def downgrade() -> None:
    with_lock_retries("task_rubric_item,task_submission_review")
    op.drop_column("task_submission_review", "auto_scores")
    op.drop_column("task_rubric_item", "config")
    op.drop_column("task_rubric_item", "kind")
