"""add block domain (dual tree + refs)

Adds the append-only ``block`` substrate: one table with two self-referential
trees (reply_to / struct_parent) plus a polymorphic ``block_ref`` table.
Only block-related DDL is kept here; unrelated autogenerate drift (FTS
indexes, task_access_domain FK, etc.) was intentionally removed.

Revision ID: 51bafd846c36
Revises: f6b7c8d9e0a1
Create Date: 2026-07-06 05:14:48.691708

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '51bafd846c36'
down_revision: Union[str, Sequence[str], None] = 'f6b7c8d9e0a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SEQUENCE IF NOT EXISTS block_seq")
    op.execute("CREATE SEQUENCE IF NOT EXISTS block_ref_seq")
    op.create_table(
        'block',
        sa.Column('id', sa.BigInteger(), server_default=sa.text("nextval('block_seq')"), nullable=False),
        sa.Column('project_id', sa.BigInteger(), nullable=False),
        sa.Column('thread_id', sa.BigInteger(), nullable=True),
        sa.Column('kind', sa.SmallInteger(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('author_id', sa.BigInteger(), nullable=False),
        sa.Column('author_kind', sa.SmallInteger(), nullable=False),
        sa.Column('reply_to_id', sa.BigInteger(), nullable=True),
        sa.Column('struct_parent_id', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['reply_to_id'], ['block.id'], ),
        sa.ForeignKeyConstraint(['struct_parent_id'], ['block.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_block_project_id'), 'block', ['project_id'], unique=False)
    op.create_index(op.f('ix_block_reply_to_id'), 'block', ['reply_to_id'], unique=False)
    op.create_index(op.f('ix_block_struct_parent_id'), 'block', ['struct_parent_id'], unique=False)
    op.create_index(op.f('ix_block_thread_id'), 'block', ['thread_id'], unique=False)
    op.create_table(
        'block_ref',
        sa.Column('id', sa.BigInteger(), server_default=sa.text("nextval('block_ref_seq')"), nullable=False),
        sa.Column('from_block_id', sa.BigInteger(), nullable=False),
        sa.Column('to_target_type', sa.String(length=50), nullable=False),
        sa.Column('to_target_id', sa.BigInteger(), nullable=False),
        sa.Column('rel', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['from_block_id'], ['block.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_block_ref_from_block_id'), 'block_ref', ['from_block_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_block_ref_from_block_id'), table_name='block_ref')
    op.drop_table('block_ref')
    op.drop_index(op.f('ix_block_thread_id'), table_name='block')
    op.drop_index(op.f('ix_block_struct_parent_id'), table_name='block')
    op.drop_index(op.f('ix_block_reply_to_id'), table_name='block')
    op.drop_index(op.f('ix_block_project_id'), table_name='block')
    op.drop_table('block')
    op.execute("DROP SEQUENCE IF EXISTS block_ref_seq")
    op.execute("DROP SEQUENCE IF EXISTS block_seq")
