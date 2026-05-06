"""add_video_url_to_task

Revision ID: 99da558b24a8
Revises: c3a1b2d4e5f6
Create Date: 2026-05-02 16:05:19.640957

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '99da558b24a8'
down_revision: Union[str, Sequence[str], None] = 'c3a1b2d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('task', sa.Column('video_url', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('task', 'video_url')
