"""the two old author values leave blocks.author_type

`AuthorType` used to carry `human` and `ai` next to each other, and they were the
same thing: a participant said something. Which participant is the signature's
question (`blocks.author`, a handle), not the column's. The previous release
added `participant` and moved every writer onto it; this one drops the two names
from the enum, so the rows that still carry them have to be rewritten.

They have to be rewritten because `Enum(AuthorType, native_enum=False)` binds the
Python enum: a row still reading `human` comes back as a `LookupError` the moment
anything selects it, not as a wrong avatar.

The rewrite is one-way and one-off. Nothing has written `human` or `ai` since the
previous release — that was the whole point of splitting this off from the change
that added `participant` — so no row of the old shape can appear behind the
migration, and there is no follow-up pass to schedule.

`system` is deliberately left alone. It is the name the platform's own events are
still written with; this release only puts `platform` into the enum beside it, so
that the release that moves the writers has a previous image able to read what
they write. `deploy/deploy-docker.sh` runs `alembic upgrade head` before it swaps
the containers, so during the window it is the previous image that reads whatever
this migration wrote — and that image has no `platform` member. Rewriting those
rows here would hand it a `LookupError` for nearly every room's timeline, with a
rollback that swaps the image back without running a downgrade. The rewrite
belongs two releases out, together with dropping the `system` member: by then
nothing writes `system` any more, and the image serving that window knows both
names.

One full pass over `blocks` with no index to help it. The predicate is not
selective enough for an index to be worth adding for a single statement, and
migrations run without a statement timeout, so it finishes — on a large
deployment simply slowly, which is worth knowing before the deploy rather than
during it.

There is no downgrade. Going back would mean deciding which of `human` and `ai`
each rewritten row used to be, and that distinction is exactly what this design
says the column never carried: the answer lives in `blocks.author`.

Revision ID: c1a7e05d4b83
Revises: a7f1c0d4e2b9
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c1a7e05d4b83"
down_revision: str | Sequence[str] | None = "a7f1c0d4e2b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE blocks SET author_type = 'participant' "
            "WHERE author_type IN ('human', 'ai')"
        )
    )


def downgrade() -> None:
    """不回退：见模块 docstring。"""
