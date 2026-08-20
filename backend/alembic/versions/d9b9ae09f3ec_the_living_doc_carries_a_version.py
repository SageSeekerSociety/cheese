"""the living doc carries a version

The living doc is written whole, every time — there is no partial edit of it,
by a person or by 芝士. That makes every write a total overwrite, and every
overwrite based on a stale read a total loss: 芝士 reads the doc at the top of a
turn, works for ten minutes, and sets back a document assembled from what the
doc said before the person edited it.

``doc_version`` counts the writes, and a writer must send the one it read. The
UPDATE is conditional on it, so the second of two writers based on the same
version is refused rather than winning silently.

Every existing block starts at 1 — the counter only ever moves on kind=doc, and
one is the number of times those docs have been written as far as anyone
holding a version can tell.

Revision ID: d9b9ae09f3ec
Revises: a71c6e0b93d4
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d9b9ae09f3ec"
down_revision: str | None = "a71c6e0b93d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "blocks",
        sa.Column("doc_version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("blocks", "doc_version")
