"""Links between a project's documents

Revision ID: 1478f65e1004
Revises: 4e8b1d6a2c90
Create Date: 2026-10-17

A document could reference channels, tasks and 支线 with `<#id>` but not
another document, and nothing recorded what referenced what (#3827).
``document_links`` holds, for each document, the documents of its project its
stored text references; the backend rewrites a document's rows on every store.
The rows the documents already written imply are filled in here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "1478f65e1004"
down_revision: str | Sequence[str] | None = "4e8b1d6a2c90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The foreign keys lock `documents` briefly; safe to repeat.
    with_lock_retries("documents")
    if "document_links" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "document_links",
            sa.Column(
                "source_id",
                sa.Uuid(),
                sa.ForeignKey(
                    "documents.id",
                    ondelete="CASCADE",
                    name="fk_document_links_source_id_documents",
                ),
                primary_key=True,
            ),
            sa.Column(
                "target_id",
                sa.Uuid(),
                sa.ForeignKey(
                    "documents.id",
                    ondelete="CASCADE",
                    name="fk_document_links_target_id_documents",
                ),
                primary_key=True,
            ),
        )
        op.create_index("ix_document_links_target_id", "document_links", ["target_id"])
    op.execute(
        """
        INSERT INTO document_links (source_id, target_id)
        SELECT DISTINCT d.id, t.id
        FROM documents d
        CROSS JOIN LATERAL regexp_matches(
            d.content, '<#([0-9a-fA-F-]{36})>', 'g'
        ) AS m(ref)
        JOIN documents t
          ON t.id::text = lower(m.ref[1])
         AND t.project_id = d.project_id
         AND t.id <> d.id
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_document_links_target_id", "document_links")
    op.drop_table("document_links")
