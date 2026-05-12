"""add FTS GIN indexes for question, topic, and team

Revision ID: b1f2a3c4d5e6
Revises: 5a3b7c9d1e2f
Create Date: 2026-05-10 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1f2a3c4d5e6"
down_revision: str | Sequence[str] | None = "5a3b7c9d1e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add GIN indexes for full-text search on question, topic, and team."""
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_question_fts "
        "ON question USING gin ("
        "to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(content, ''))"
        ")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_topic_fts "
        "ON topic USING gin ("
        "to_tsvector('simple', coalesce(name, ''))"
        ")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_team_fts "
        "ON team USING gin ("
        "to_tsvector('simple', coalesce(name, ''))"
        ")"
    )


def downgrade() -> None:
    """Drop FTS GIN indexes."""
    op.execute("DROP INDEX IF EXISTS ix_question_fts")
    op.execute("DROP INDEX IF EXISTS ix_topic_fts")
    op.execute("DROP INDEX IF EXISTS ix_team_fts")
