"""document_mentions: the people a document's text told they were mentioned

Revision ID: d4c8e1a7b3f2
Revises: b29905fee2b7
Create Date: 2026-10-14

One row per (document, handle), written the first time a person's save adds
``<@handle>`` to the document while that person can open it. A new table, so
no backfill: documents written before it told nobody.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "d4c8e1a7b3f2"
down_revision: str | Sequence[str] | None = "b29905fee2b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The foreign key takes a lock on documents, which the live backend writes.
    with_lock_retries("documents")
    op.create_table(
        "document_mentions",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("handle", sa.String(128), nullable=False),
        sa.Column("notified_by", sa.String(128), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
            name="fk_document_mentions_document_id_documents",
        ),
        sa.PrimaryKeyConstraint("document_id", "handle"),
    )


def downgrade() -> None:
    op.drop_table("document_mentions")
