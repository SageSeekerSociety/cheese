"""add_video_url_to_task

Revision ID: 99da558b24a8
Revises: c3a1b2d4e5f6
Create Date: 2026-05-02 16:05:19.640957

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "99da558b24a8"
down_revision: str | Sequence[str] | None = "c3a1b2d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("task", sa.Column("video_url", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("task", "video_url")
