"""Drop what the release before documents belonged to a team still needed

Revision ID: 569e06806d03
Revises: 508af8ca8a00
Create Date: 2026-10-18

``ddde8e77a6af`` moved the team knowledge base into documents (#3774) while
the release before it was still serving. That release read the knowledge
tables and created documents without a team, so the migration kept the
tables and added a trigger that gave such a document its project's team.
Nothing that predates it serves any more: the trigger, its function and the
three knowledge tables go.
"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

revision: str = "569e06806d03"
down_revision: str | Sequence[str] | None = "508af8ca8a00"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KNOWLEDGE_TABLES = ("knowledge_upvote", "knowledge_label", "knowledge")


def upgrade() -> None:
    with_lock_retries("documents")
    op.execute("DROP TRIGGER IF EXISTS documents_team_from_project ON documents")
    op.execute("DROP FUNCTION IF EXISTS documents_team_from_project()")
    for name in KNOWLEDGE_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {name} CASCADE")


def downgrade() -> None:
    raise NotImplementedError("The knowledge base is gone; restore from a backup")
