"""Voice inputs: each recording a person turned into text at the composer.

Revision ID: 302a73cbc344
Revises: b4d7e19a3c58
"""

import sqlalchemy as sa

from alembic import op

revision = "302a73cbc344"
down_revision = "c7cfcf6cbdc8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "voice_inputs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("seconds", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_voice_inputs_user_id_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_voice_inputs_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_voice_inputs_conversation_id_conversations",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_voice_inputs_project_id", "voice_inputs", ["project_id"])
    op.create_index(
        "ix_voice_inputs_conversation_id", "voice_inputs", ["conversation_id"]
    )
    op.create_index(
        "ix_voice_inputs_user_created_at", "voice_inputs", ["user_id", "created_at"]
    )


def downgrade():
    op.drop_table("voice_inputs")
