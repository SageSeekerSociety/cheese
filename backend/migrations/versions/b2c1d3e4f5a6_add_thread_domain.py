"""add thread domain (nestable conversation + membership/attention)

Adds the 2.0 conversation container: ``thread`` (project-anchored, nestable)
and ``thread_membership`` (users + agents, with role and attention policy).
Hand-written to avoid unrelated autogenerate drift.

Revision ID: b2c1d3e4f5a6
Revises: 51bafd846c36
Create Date: 2026-07-06 05:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c1d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = '51bafd846c36'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SEQUENCE IF NOT EXISTS thread_seq")
    op.execute("CREATE SEQUENCE IF NOT EXISTS thread_membership_seq")
    op.create_table(
        'thread',
        sa.Column('id', sa.BigInteger(), server_default=sa.text("nextval('thread_seq')"), nullable=False),
        sa.Column('project_id', sa.BigInteger(), nullable=False),
        sa.Column('parent_thread_id', sa.BigInteger(), nullable=True),
        sa.Column('kind', sa.SmallInteger(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('created_by_id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['parent_thread_id'], ['thread.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_thread_project_id'), 'thread', ['project_id'], unique=False)
    op.create_index(op.f('ix_thread_parent_thread_id'), 'thread', ['parent_thread_id'], unique=False)
    op.create_table(
        'thread_membership',
        sa.Column('id', sa.BigInteger(), server_default=sa.text("nextval('thread_membership_seq')"), nullable=False),
        sa.Column('thread_id', sa.BigInteger(), nullable=False),
        sa.Column('member_id', sa.BigInteger(), nullable=False),
        sa.Column('member_kind', sa.SmallInteger(), nullable=False),
        sa.Column('role', sa.SmallInteger(), nullable=False),
        sa.Column('attention_policy', sa.SmallInteger(), nullable=False),
        sa.Column('attention_window_seconds', sa.Integer(), nullable=True),
        sa.Column('cursor_block_id', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['thread_id'], ['thread.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('thread_id', 'member_id', 'member_kind', name='uq_thread_member'),
    )
    op.create_index(op.f('ix_thread_membership_thread_id'), 'thread_membership', ['thread_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_thread_membership_thread_id'), table_name='thread_membership')
    op.drop_table('thread_membership')
    op.drop_index(op.f('ix_thread_parent_thread_id'), table_name='thread')
    op.drop_index(op.f('ix_thread_project_id'), table_name='thread')
    op.drop_table('thread')
    op.execute("DROP SEQUENCE IF EXISTS thread_membership_seq")
    op.execute("DROP SEQUENCE IF EXISTS thread_seq")
