"""Agent read cursors: each teammate follows a conversation with its own eyes.

Revision ID: 9c1f2a7b4e60
Revises: 302a73cbc344
"""

import sqlalchemy as sa

from alembic import op

revision = "9c1f2a7b4e60"
down_revision = "302a73cbc344"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_reads",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("agent_handle", sa.String(length=64), nullable=False),
        sa.Column("read_through_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_through_block_id", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_agent_reads_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("conversation_id", "agent_handle"),
    )


def downgrade():
    op.drop_table("agent_reads")
