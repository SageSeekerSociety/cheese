"""docs_questions, service_credentials —— what the docs site's server side keeps

问芝士 records each question and how it went (answered, no_match, failed), so the
people who maintain the docs can see what readers ask that the docs do not
cover; rows are purged after DOCS_QUESTION_RETENTION_DAYS. The user reference
is SET NULL on delete: removing an account removes who asked, not the signal.

service_credentials holds credentials the platform mints for itself at run time
and must not mint twice — first, the gateway virtual key 问芝士 calls with.

Revision ID: 52b13868b0e8
Revises: d4e7a2c91b35
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "52b13868b0e8"
down_revision: str | Sequence[str] | None = "d4e7a2c91b35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "docs_questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("page", sa.String(128), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column(
            "sources",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_docs_questions_created_at", "docs_questions", ["created_at"])
    op.create_index(
        "ix_docs_questions_user_created", "docs_questions", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_docs_questions_outcome_created", "docs_questions", ["outcome", "created_at"]
    )
    op.create_table(
        "service_credentials",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("service_credentials")
    op.drop_index("ix_docs_questions_outcome_created", table_name="docs_questions")
    op.drop_index("ix_docs_questions_user_created", table_name="docs_questions")
    op.drop_index("ix_docs_questions_created_at", table_name="docs_questions")
    op.drop_table("docs_questions")
