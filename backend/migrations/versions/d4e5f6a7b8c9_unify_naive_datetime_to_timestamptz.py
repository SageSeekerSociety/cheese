"""unify naive datetime columns to timestamptz

Revision ID: d4e5f6a7b8c9
Revises: c2d3e4f5a6b7
Create Date: 2026-05-10 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# All table/column pairs that are currently TIMESTAMP WITHOUT TIME ZONE
# and need to become TIMESTAMP WITH TIME ZONE.
_COLUMNS_TO_CONVERT: list[tuple[str, str]] = [
    ("ai_conversation", "created_at"),
    ("ai_conversation", "updated_at"),
    ("ai_conversation", "deleted_at"),
    ("ai_message", "created_at"),
    ("ai_message", "updated_at"),
    ("ai_message", "deleted_at"),
    ("comment", "created_at"),
    ("comment", "updated_at"),
    ("comment", "deleted_at"),
    ("llm_call_log", "created_at"),
    ("notification", "created_at"),
    ("notification", "updated_at"),
    ("notification", "deleted_at"),
    ("project", "created_at"),
    ("project", "updated_at"),
    ("project", "deleted_at"),
    ("project_membership", "created_at"),
    ("project_membership", "updated_at"),
    ("project_membership", "deleted_at"),
    ("space", "created_at"),
    ("space", "updated_at"),
    ("space", "deleted_at"),
    ("space_admin_relation", "created_at"),
    ("space_admin_relation", "updated_at"),
    ("space_admin_relation", "deleted_at"),
    ("space_categories", "archived_at"),
    ("space_categories", "created_at"),
    ("space_categories", "updated_at"),
    ("space_categories", "deleted_at"),
    ("space_user_rank", "created_at"),
    ("space_user_rank", "updated_at"),
    ("space_user_rank", "deleted_at"),
    ("task", "deadline"),
    ("task", "registration_start_at"),
    ("task", "created_at"),
    ("task", "updated_at"),
    ("task", "deleted_at"),
    ("task_ai_advice", "created_at"),
    ("task_ai_advice", "updated_at"),
    ("task_ai_advice_context", "created_at"),
    ("task_ai_advice_context", "updated_at"),
    ("task_ai_advice_context", "deleted_at"),
    ("task_membership", "created_at"),
    ("task_membership", "updated_at"),
    ("task_membership", "deadline"),
    ("task_membership", "deleted_at"),
    ("task_submission", "created_at"),
    ("task_submission", "updated_at"),
    ("task_submission", "deleted_at"),
    ("task_submission_entry", "created_at"),
    ("task_submission_entry", "updated_at"),
    ("task_submission_entry", "deleted_at"),
    ("task_submission_review", "created_at"),
    ("task_submission_review", "updated_at"),
    ("task_submission_review", "deleted_at"),
    ("task_topics_relation", "created_at"),
    ("task_topics_relation", "updated_at"),
    ("task_topics_relation", "deleted_at"),
    ("team", "created_at"),
    ("team", "updated_at"),
    ("team", "deleted_at"),
    ("team_membership_application", "created_at"),
    ("team_membership_application", "updated_at"),
    ("team_membership_application", "deleted_at"),
    ("team_user_relation", "created_at"),
    ("team_user_relation", "updated_at"),
    ("team_user_relation", "deleted_at"),
    ("user_ai_quota", "last_reset_time"),
    ("user_ai_quota", "created_at"),
    ("user_ai_quota", "updated_at"),
    ("user_ai_quota", "deleted_at"),
    ("user_o_auth_connection", "token_expires"),
]


def upgrade() -> None:
    """Convert all naive TIMESTAMP columns to TIMESTAMP WITH TIME ZONE."""
    for table, column in _COLUMNS_TO_CONVERT:
        op.alter_column(
            table,
            column,
            existing_type=postgresql.TIMESTAMP(),
            type_=sa.DateTime(timezone=True),
            existing_nullable=column
            in (
                "deleted_at",
                "updated_at",
                "deadline",
                "registration_start_at",
                "last_reset_time",
                "archived_at",
                "token_expires",
            ),
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )


def downgrade() -> None:
    """Revert TIMESTAMPTZ columns back to naive TIMESTAMP."""
    for table, column in reversed(_COLUMNS_TO_CONVERT):
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            type_=postgresql.TIMESTAMP(),
            existing_nullable=column
            in (
                "deleted_at",
                "updated_at",
                "deadline",
                "registration_start_at",
                "last_reset_time",
                "archived_at",
                "token_expires",
            ),
        )
