"""add passkey credential table

Revision ID: add_passkey_001
Revises: add_ai_chat_001
Create Date: 2026-01-02
"""

import sqlalchemy as sa
from alembic import op

revision = "add_passkey_001"
down_revision = "add_ai_chat_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "passkey_credential",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("device_name", sa.String(255), nullable=True),
        sa.Column("transports", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_passkey_credential_user_id", "passkey_credential", ["user_id"])
    op.create_index(
        "ix_passkey_credential_credential_id",
        "passkey_credential",
        ["credential_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_passkey_credential_credential_id", table_name="passkey_credential")
    op.drop_index("ix_passkey_credential_user_id", table_name="passkey_credential")
    op.drop_table("passkey_credential")
