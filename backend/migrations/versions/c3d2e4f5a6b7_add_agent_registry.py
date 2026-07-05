"""add agent registry

Adds the project-owned ``agent`` table (the registry of AI actors). A clone
references its primary via ``parent_agent_id``. Hand-written to avoid unrelated
autogenerate drift.

Revision ID: c3d2e4f5a6b7
Revises: b2c1d3e4f5a6
Create Date: 2026-07-06 05:38:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d2e4f5a6b7'
down_revision: Union[str, Sequence[str], None] = 'b2c1d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SEQUENCE IF NOT EXISTS agent_seq")
    op.create_table(
        'agent',
        sa.Column('id', sa.BigInteger(), server_default=sa.text("nextval('agent_seq')"), nullable=False),
        sa.Column('project_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('role_prompt', sa.Text(), nullable=False),
        sa.Column('adapter_kind', sa.SmallInteger(), nullable=False),
        sa.Column('status', sa.SmallInteger(), nullable=False),
        sa.Column('parent_agent_id', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['parent_agent_id'], ['agent.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_agent_project_id'), 'agent', ['project_id'], unique=False)
    op.create_index(op.f('ix_agent_parent_agent_id'), 'agent', ['parent_agent_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_agent_parent_agent_id'), table_name='agent')
    op.drop_index(op.f('ix_agent_project_id'), table_name='agent')
    op.drop_table('agent')
    op.execute("DROP SEQUENCE IF EXISTS agent_seq")
