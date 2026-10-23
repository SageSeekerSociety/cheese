"""feedback_attachments — files a report carries

方案稿 §8.7 left attachment uploads out on purpose, with one requirement
written down for whoever built them: a private report's files must not be
reachable from a public address. So the bytes go to `blob_store` (no public
URL) and this table is the only pointer to them.

`feedback_id` is nullable because the form uploads a file the moment it is
picked and only files the report on submit (see the model's docstring).

Revision ID: b41d7a9c2e63
Revises: 9e2a4b7c1d80
Create Date: 2026-10-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b41d7a9c2e63"
down_revision: str | Sequence[str] | None = "9e2a4b7c1d80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "feedback_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("feedback_id", sa.Uuid(), nullable=True),
        sa.Column("uploader_handle", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("location", sa.String(length=16), nullable=False),
        sa.Column("blob_key", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["feedback_id"],
            ["feedback.id"],
            ondelete="CASCADE",
            name="fk_feedback_attachments_feedback_id_feedback",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_feedback_attachments_feedback_created",
        "feedback_attachments",
        ["feedback_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_feedback_attachments_feedback_created",
        table_name="feedback_attachments",
    )
    op.drop_table("feedback_attachments")
