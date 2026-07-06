"""add review (验收 of a thread)

Isolated project-scoped review records. Hand-written to avoid unrelated
autogenerate drift.

Revision ID: a7b6c8d9e0f1
Revises: f6a5b7c8d9e0
Create Date: 2026-07-06 08:12:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7b6c8d9e0f1'
down_revision: Union[str, Sequence[str], None] = 'f6a5b7c8d9e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SEQUENCE IF NOT EXISTS review_seq")
    op.create_table(
        'review',
        sa.Column('id', sa.BigInteger(), server_default=sa.text("nextval('review_seq')"), nullable=False),
        sa.Column('project_id', sa.BigInteger(), nullable=False),
        sa.Column('thread_id', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.SmallInteger(), nullable=False),
        sa.Column('requested_by_id', sa.BigInteger(), nullable=False),
        sa.Column('decided_by_id', sa.BigInteger(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_review_project_id'), 'review', ['project_id'], unique=False)
    op.create_index(op.f('ix_review_thread_id'), 'review', ['thread_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_review_thread_id'), table_name='review')
    op.drop_index(op.f('ix_review_project_id'), table_name='review')
    op.drop_table('review')
    op.execute("DROP SEQUENCE IF EXISTS review_seq")
