"""block meta is an object

c9f4a2b7e130 stamped ``in_room: false`` on every non-system event with

    COALESCE(meta::jsonb, '{}'::jsonb) || '{"in_room": false}'::jsonb

COALESCE replaces SQL NULL and nothing else. A row holding the JSON literal
``null`` — which is what the ORM writes for ``meta=None`` — kept it, and jsonb's
``||`` with a non-object on the left does not merge, it concatenates into an
array: ``[null, {"in_room": false}]``. On dev that hit 32 rows, all of them the
hallucinated-@ warning, and a topic holding one answered ``GET
/topics/{id}/blocks`` and ``/tasks`` with a 500 for two weeks (08-27 to 09-02),
because one row failing ``BlockOut`` takes the whole timeline down with it.

The fix is the object that was meant: element 1 of the array. Only that exact
shape is touched — an array of any other shape was never produced, and
guessing at one would be a second corruption.

Revision ID: 89fb9b9a11e0
Revises: a7c1e93b4d20
Create Date: 2026-09-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "89fb9b9a11e0"
down_revision: str | Sequence[str] | None = "a7c1e93b4d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `meta` is json, not jsonb: json_typeof / json_array_length, and `->` on
    # json returns json.
    op.execute(
        """
        UPDATE blocks
        SET meta = meta -> 1
        WHERE meta IS NOT NULL
          AND json_typeof(meta) = 'array'
          AND json_array_length(meta) = 2
          AND json_typeof(meta -> 0) = 'null'
          AND json_typeof(meta -> 1) = 'object'
        """
    )


def downgrade() -> None:
    # The array was never a valid state, so there is nothing to put back.
    pass
