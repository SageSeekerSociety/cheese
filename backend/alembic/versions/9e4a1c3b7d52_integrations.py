"""integrations / mail_drafts —— a person's mailbox and Feishu, lent to projects

Revision ID: 9e4a1c3b7d52
Revises: 7b3e9d2c4a10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "9e4a1c3b7d52"
down_revision: str | Sequence[str] | None = "7b3e9d2c4a10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integrations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_handle", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("grants", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_integrations_owner_user_id", "integrations", ["owner_user_id"])
    op.create_table(
        "mail_drafts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "integration_id",
            sa.Uuid(),
            sa.ForeignKey("integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("to", postgresql.JSONB(), nullable=False),
        sa.Column("cc", postgresql.JSONB(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("attachments", postgresql.JSONB(), nullable=False),
        sa.Column("in_reply_to", sa.Text(), nullable=True),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("confirmed_by", sa.String(64), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mail_drafts_integration_id", "mail_drafts", ["integration_id"])


def downgrade() -> None:
    op.drop_table("mail_drafts")
    op.drop_table("integrations")
