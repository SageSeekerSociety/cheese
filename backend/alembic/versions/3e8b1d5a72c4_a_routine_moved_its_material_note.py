"""What a rule used to keep about materials and about where results go moves
into 「补充说明」

Revision ID: 3e8b1d5a72c4
Revises: 0d7f3a9c41b6
Create Date: 2026-10-15

The form asks for a goal and, optionally, a note. It no longer asks 「使用哪些
资料」 or 「结果放在频道的哪个目录」: each run decides those for itself. A rule
that was told, back then, still has to be told — what was stored in
``context_scope`` and in ``output_dir`` is folded into the one free-text field
the teammate reads, so 「照旧生效」.

The result line is the one that has to be spelled out: 「频道 周报」 is not a
sentence, and the teammate reads the note as written. A rule whose folder was
empty adds nothing.

Idempotent by construction: after the first run the folded line is in
``context_scope``, and the ``WHERE`` skips any row that already carries it
(``output_dir`` is left where it is — unmapped, and gone two releases from
now, the same as this column's old name).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "3e8b1d5a72c4"
down_revision: str | Sequence[str] | None = "0d7f3a9c41b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FOLD = """
UPDATE routines
   SET context_scope = CASE
         WHEN btrim(coalesce(context_scope, '')) = ''
           THEN '结果保存到：频道 ' || btrim(output_dir)
         ELSE btrim(context_scope) || E'\\n结果保存到：频道 ' || btrim(output_dir)
       END
 WHERE btrim(coalesce(output_dir, '')) <> ''
   AND position(
         '结果保存到：频道 ' || btrim(output_dir) IN coalesce(context_scope, '')
       ) = 0
"""


def upgrade() -> None:
    op.execute(_FOLD)


def downgrade() -> None:
    """Nothing to undo: both texts now live in one column.

    Unfolding would have to guess which part of the note came from a folder —
    and the note may have been edited since, by a person. The old columns are
    still there with what they held, so a downgrade costs nothing but the
    editor's own words: leave the data where it is.
    """
