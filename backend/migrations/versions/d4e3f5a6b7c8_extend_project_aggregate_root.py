"""extend project into the 2.0 aggregate root

Adds agent-orchestration fields to the existing ``project`` table: ai_mode,
approval_policy (both defaulted so every legacy project is well-defined with
AI off) and a nullable root_thread_id. Additive only. Hand-written.

Revision ID: d4e3f5a6b7c8
Revises: c3d2e4f5a6b7
Create Date: 2026-07-06 05:42:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e3f5a6b7c8'
down_revision: Union[str, Sequence[str], None] = 'c3d2e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'project',
        sa.Column('ai_mode', sa.SmallInteger(), nullable=False, server_default='0'),
    )
    op.add_column(
        'project',
        sa.Column('approval_policy', sa.SmallInteger(), nullable=False, server_default='0'),
    )
    op.add_column(
        'project',
        sa.Column('root_thread_id', sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('project', 'root_thread_id')
    op.drop_column('project', 'approval_policy')
    op.drop_column('project', 'ai_mode')
