"""system leaves blocks.author_type

`system` was the name the platform's own events were written with. It named the
process rather than the author, and the author is the platform: the previous
release put `platform` into the enum beside it and moved every writer onto it,
so nothing has written `system` since. This one rewrites the rows that still
carry it and drops the member.

They have to be rewritten because `Enum(AuthorType, native_enum=False)` binds the
Python enum: a row still reading `system` comes back as a `LookupError` the
moment anything selects it, not as a wrong avatar. Nearly every room holds a
platform event, so leaving them would take nearly every timeline with it.

The rewrite is one-way and one-off, and it is safe in the deploy window for the
same reason the previous rewrite was. `deploy/deploy-docker.sh` runs `alembic
upgrade head` before it swaps the containers, so during the window it is the
previous image that serves what this migration wrote — and that image has had
`platform` in its enum since the release before last, and writes nothing but
`platform` itself. So no row of the old shape can appear behind the migration,
and there is no follow-up pass to schedule.

It is also idempotent: the `WHERE` names the value being retired rather than
"anything that is not participant", so a deploy that is retried, or rolled back
and attempted again, runs it a second time over rows it has already rewritten
and changes nothing. Writing the predicate the other way would pass the first
pass and turn every participant's message into a platform event on the second.

One full pass over `blocks` with no index to help it. The predicate is not
selective enough for an index to be worth adding for a single statement, and
migrations run without a statement timeout, so it finishes — on a large
deployment simply slowly, which is worth knowing before the deploy rather than
during it.

There is no downgrade. Going back would mean deciding which of the platform's
events were written before the rename and which after, and nothing in the table
records that: `blocks.author_type` stores one value per author, not a history of
what it used to be called.

Revision ID: 2895c4967ca4
Revises: d7b3f0a9c651
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2895c4967ca4"
down_revision: str | Sequence[str] | None = "d7b3f0a9c651"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE blocks SET author_type = 'platform' WHERE author_type = 'system'"
        )
    )


def downgrade() -> None:
    """不回退：见模块 docstring。"""
