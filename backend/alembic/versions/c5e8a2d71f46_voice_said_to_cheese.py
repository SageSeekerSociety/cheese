"""A recording can be said to a person's own 芝士, in no project

Voice input was a message in a room, so each recording kept the project it was
said in. A person can now also speak to their own 芝士 from any page, which is
in no project: `voice_inputs.project_id` may be empty. The daily allowance
still reads a person's recordings across all of them.

Dropping NOT NULL changes only the catalog; the previous release, which always
writes a project, is unaffected.

Revision ID: c5e8a2d71f46
Revises: b7f3c1a90e42
Create Date: 2026-10-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c5e8a2d71f46"
down_revision: str | Sequence[str] | None = "b7f3c1a90e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("voice_inputs", "project_id", nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM voice_inputs WHERE project_id IS NULL")
    op.alter_column("voice_inputs", "project_id", nullable=False)
