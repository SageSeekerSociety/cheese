"""A document's read-only share link

Revision ID: 508af8ca8a00
Revises: ddde8e77a6af
Create Date: 2026-10-18

A document can be shared with people outside it as a read-only link (#3774).
``document_shares`` holds at most one link per document; the token is the
link, taking it back deletes the row.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "508af8ca8a00"
down_revision: str | Sequence[str] | None = "ddde8e77a6af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The foreign key locks `documents` briefly; safe to repeat.
    with_lock_retries("documents")
    if "document_shares" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "document_shares",
            sa.Column(
                "document_id",
                sa.Uuid(),
                sa.ForeignKey(
                    "documents.id",
                    ondelete="CASCADE",
                    name="fk_document_shares_document_id_documents",
                ),
                primary_key=True,
            ),
            sa.Column("token", sa.String(64), nullable=False),
            sa.Column("created_by", sa.String(128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("token", name="uq_document_shares_token"),
        )


def downgrade() -> None:
    op.drop_table("document_shares")
