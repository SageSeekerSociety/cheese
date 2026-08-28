"""events: in_room is its own field, not a shade of author_type

Whether an event shows in the conversation used to be read off `author_type`
(system = the room, ai = 现场 only). Visibility is not authorship, and the
overload meant an event genuinely written by 芝士 could not be shown without
lying about who wrote it.

Existing 现场-only events are exactly the rows the old rule hid — `kind=event`
with any `author_type` other than `system` — so this stamps `meta.in_room =
false` on them. Writing the predicate as `<> 'system'` rather than `= 'ai'`
keeps it the same set the frontend was testing: `ai` is what nearly all of them
are, but an event authored by a person (the 活动 seed) was hidden too, and
naming only `ai` would surface it in every room. Every remaining event is a room
event and needs no mark: absent means shown.

One full pass over `blocks` with no index to help it — the predicate is not
selective enough for one to be worth adding for a single statement. Migrations
run without a statement timeout, so it finishes; on a large deployment it is
simply slow, and that is worth knowing before starting the deploy rather than
during it.

Revision ID: c9f4a2b7e130
Revises: b3e1d75a4c20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9f4a2b7e130"
down_revision: str | Sequence[str] | None = "b3e1d75a4c20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE blocks "
            "SET meta = (COALESCE(meta::jsonb, '{}'::jsonb) "
            "            || '{\"in_room\": false}'::jsonb)::json "
            "WHERE kind = 'event' AND author_type <> 'system'"
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE blocks SET meta = (meta::jsonb - 'in_room')::json "
            "WHERE kind = 'event' AND meta::jsonb ? 'in_room'"
        )
    )
