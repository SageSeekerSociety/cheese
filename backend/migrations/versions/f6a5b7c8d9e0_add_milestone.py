"""add milestone (project deadline / calendar)

Isolated project-scoped deadline table. Hand-written to avoid unrelated
autogenerate drift.

Revision ID: f6a5b7c8d9e0
Revises: e5f4a6b7c8d9
Create Date: 2026-07-06 07:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6a5b7c8d9e0'
down_revision: Union[str, Sequence[str], None] = 'e5f4a6b7c8d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SEQUENCE IF NOT EXISTS milestone_seq")
    op.create_table(
        'milestone',
        sa.Column('id', sa.BigInteger(), server_default=sa.text("nextval('milestone_seq')"), nullable=False),
        sa.Column('project_id', sa.BigInteger(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('kind', sa.SmallInteger(), nullable=False),
        sa.Column('status', sa.SmallInteger(), nullable=False),
        sa.Column('created_by_id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_milestone_project_id'), 'milestone', ['project_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_milestone_project_id'), table_name='milestone')
    op.drop_table('milestone')
    op.execute("DROP SEQUENCE IF EXISTS milestone_seq")
