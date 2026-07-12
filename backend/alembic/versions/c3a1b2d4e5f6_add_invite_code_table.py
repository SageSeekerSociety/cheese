"""Add invite_code table.

Revision ID: c3a1b2d4e5f6
Revises: 219831eb75a3
Create Date: 2026-03-30
"""

import sqlalchemy as sa

from alembic import op

revision = "c3a1b2d4e5f6"
down_revision = "219831eb75a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invite_code",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(256), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_invite_code_code", "invite_code", ["code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_invite_code_code", table_name="invite_code")
    op.drop_table("invite_code")
