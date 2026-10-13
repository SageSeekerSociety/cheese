"""a question asks a list of people

Revision ID: a7c3e9d1f520
Revises: 00cc083dc9f9
Create Date: 2026-10-13

A question an agent asks (`cheese_ask`) recorded whom it waits on as
``meta.asked``: one handle, or null for nobody in particular. The agent now
chooses whom it asks — one person, several, or everyone in the channel — so
``asked`` becomes a list of handles: a handle becomes a one-element list, and
null (or a missing key) the empty list, which still reads as nobody in
particular. ``asked_all`` is not added to old rows: none of them was asked of
everyone, and an absent key reads as false.

Only question rows are touched (the same predicate as `ix_blocks_questions`,
so the index finds them), and only those not converted yet, so a rerun is a
no-op. Every question ever asked is a few thousand rows at most: one plain
statement. ``meta`` is json, not jsonb, so it is cast to jsonb to set the key
and back.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a7c3e9d1f520"
down_revision: str | Sequence[str] | None = "00cc083dc9f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Copied from `app.domain.block.indexed_rows.QUESTION_ROWS` (no app imports).
_QUESTIONS = "kind = 'message' AND (meta ->> 'options') IS NOT NULL"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE blocks
           SET meta = jsonb_set(
                   meta::jsonb,
                   '{{asked}}',
                   CASE WHEN json_typeof(meta -> 'asked') = 'string'
                        THEN jsonb_build_array(meta ->> 'asked')
                        ELSE '[]'::jsonb
                   END
               )::json
         WHERE {_QUESTIONS}
           AND json_typeof(meta) = 'object'
           AND (meta -> 'asked' IS NULL OR json_typeof(meta -> 'asked') <> 'array')
        """
    )


def downgrade() -> None:
    # A list of several people has no single-handle form: the first one is kept,
    # and ``asked_all`` is dropped with the meaning it carried.
    op.execute(
        f"""
        UPDATE blocks
           SET meta = (
                   jsonb_set(
                       meta::jsonb,
                       '{{asked}}',
                       COALESCE((meta::jsonb -> 'asked') -> 0, 'null'::jsonb)
                   ) - 'asked_all'
               )::json
         WHERE {_QUESTIONS}
           AND json_typeof(meta) = 'object'
           AND json_typeof(meta -> 'asked') = 'array'
        """
    )
