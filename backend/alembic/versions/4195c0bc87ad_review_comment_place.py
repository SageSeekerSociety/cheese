"""review comments: where in the file a comment points, for files without lines

Revision ID: 4195c0bc87ad
Revises: c5d2e8a1f4b7

A comment on a Word document, a workbook or a deck points at a page, a cell
or a slide, which line numbers cannot say. `place` holds the address in the
file's own terms for every comment: `L12-L14` for lines, `p3` for a page,
`s2` for a slide, `汇总!C5` for a cell. Existing comments are all on text
files, so they get their lines written the same way.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "4195c0bc87ad"
down_revision: str | Sequence[str] | None = "c5d2e8a1f4b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("review_comments")
    op.add_column(
        "review_comments",
        sa.Column("place", sa.String(255), nullable=False, server_default=""),
    )
    op.execute(
        "UPDATE review_comments SET place = 'L' || line_start || CASE "
        "WHEN line_end <> line_start THEN '-L' || line_end ELSE '' END "
        "WHERE place = ''"
    )


def downgrade() -> None:
    op.drop_column("review_comments", "place")
