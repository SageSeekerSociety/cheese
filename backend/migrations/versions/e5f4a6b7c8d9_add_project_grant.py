"""add project_grant (capability delegation to a project)

Records capabilities holders have shared with a project. Revocable
(revoked_at) and audited. Hand-written to avoid unrelated autogenerate drift.

Revision ID: e5f4a6b7c8d9
Revises: d4e3f5a6b7c8
Create Date: 2026-07-06 05:59:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f4a6b7c8d9'
down_revision: Union[str, Sequence[str], None] = 'd4e3f5a6b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SEQUENCE IF NOT EXISTS project_grant_seq")
    op.create_table(
        'project_grant',
        sa.Column('id', sa.BigInteger(), server_default=sa.text("nextval('project_grant_seq')"), nullable=False),
        sa.Column('project_id', sa.BigInteger(), nullable=False),
        sa.Column('granted_by_user_id', sa.BigInteger(), nullable=False),
        sa.Column('resource_type', sa.String(length=50), nullable=False),
        sa.Column('resource_id', sa.BigInteger(), nullable=True),
        sa.Column('action', sa.String(length=20), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_project_grant_project_id'), 'project_grant', ['project_id'], unique=False)
    op.create_index(op.f('ix_project_grant_granted_by_user_id'), 'project_grant', ['granted_by_user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_project_grant_granted_by_user_id'), table_name='project_grant')
    op.drop_index(op.f('ix_project_grant_project_id'), table_name='project_grant')
    op.drop_table('project_grant')
    op.execute("DROP SEQUENCE IF EXISTS project_grant_seq")
